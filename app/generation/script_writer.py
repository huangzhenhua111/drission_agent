from __future__ import annotations

import json
from pprint import pformat


class ScriptWriter:
    def render(
        self,
        captured_actions: list[dict],
        *,
        browser_user_data_path: str | None = None,
        browser_debug_port: int | None = None,
        wait_for_login: bool = False,
        login_timeout_seconds: int = 300,
        action_delay_seconds: float = 0,
    ) -> str:
        actions_literal = pformat(captured_actions, width=100, sort_dicts=False)
        return SCRIPT_TEMPLATE.format(
            actions=actions_literal,
            browser_user_data_path=repr(browser_user_data_path),
            browser_debug_port=repr(browser_debug_port),
            wait_for_login=repr(wait_for_login),
            login_timeout_seconds=repr(login_timeout_seconds),
            action_delay_seconds=repr(float(action_delay_seconds)),
        )


SCRIPT_TEMPLATE = '''from __future__ import annotations

import os
import platform
import json
import re
import shutil
import time
from pathlib import Path
from typing import Any

from DrissionPage import ChromiumOptions, ChromiumPage


ACTIONS = {actions}
DEFAULT_BROWSER_USER_DATA_PATH = {browser_user_data_path}
DEFAULT_BROWSER_DEBUG_PORT = {browser_debug_port}
DEFAULT_WAIT_FOR_LOGIN = {wait_for_login}
DEFAULT_LOGIN_TIMEOUT_SECONDS = {login_timeout_seconds}
DEFAULT_ACTION_DELAY_SECONDS = {action_delay_seconds}


DEFAULT_PAGE_TIMEOUT = 20
DEFAULT_ELEMENT_TIMEOUT = 10
DEFAULT_FAST_SELECTOR_TIMEOUT = 0.3
BASE_DIR = Path(__file__).resolve().parent
METRICS_PATH = BASE_DIR / "generated_script_metrics.json"
METRICS = {{
    "schema_version": 1,
    "llm_calls": {{
        "planner": 0,
        "replanner": 0,
        "visual_grounding": 0,
        "visual_verification": 0,
        "total": 0,
    }},
    "actions": [],
    "selector_lookup_count": 0,
    "selector_lookup_seconds": 0.0,
    "visual_coordinate_clicks": 0,
    "action_delay_seconds": 0.0,
    "postconditions": [],
    "current_action": None,
    "success": False,
}}


def env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {{"1", "true", "yes", "on"}}


def build_page() -> ChromiumPage:
    options = ChromiumOptions()
    if hasattr(options, "headless"):
        options.headless(env_bool("BROWSER_HEADLESS"))
    browser_path = resolve_browser_path()
    if browser_path and hasattr(options, "set_browser_path"):
        options.set_browser_path(str(browser_path))
    configured_profile = os.environ.get("BROWSER_USER_DATA_PATH") or DEFAULT_BROWSER_USER_DATA_PATH
    profile_path = Path(configured_profile).expanduser() if configured_profile else BASE_DIR / "browser_profile"
    if not profile_path.is_absolute():
        profile_path = BASE_DIR / profile_path
    profile_path = profile_path.resolve()
    profile_path.mkdir(parents=True, exist_ok=True)
    if hasattr(options, "set_user_data_path"):
        options.set_user_data_path(str(profile_path))
    configured_port = os.environ.get("BROWSER_DEBUG_PORT") or DEFAULT_BROWSER_DEBUG_PORT or "19222"
    if hasattr(options, "set_address"):
        options.set_address(f"127.0.0.1:{{configured_port}}")
    return ChromiumPage(options)


def resolve_browser_path() -> Path | None:
    configured = os.environ.get("BROWSER_PATH")
    if configured:
        candidate = Path(configured).expanduser().resolve()
        if candidate.exists():
            return candidate
    for executable in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
        discovered = shutil.which(executable)
        if discovered:
            return Path(discovered).resolve()
    for candidate in (
        Path.home() / ".local/bin/google-chrome",
        Path.home() / ".local/opt/google-chrome-deb/opt/google/chrome/google-chrome",
    ):
        if candidate.exists():
            return candidate.resolve()
    paths = []
    if platform.system() == "Windows":
        paths = [
            r"C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
            r"C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe",
            r"C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
            r"C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
        ]
    for path in paths:
        candidate = Path(path)
        if candidate.exists():
            return candidate
    return None


def normalize_selector(selector: str) -> str:
    selector = selector.strip()
    if selector.startswith("text="):
        return "text:" + selector[len("text="):]
    return selector


def selector_indexes(action: dict) -> dict[str, int]:
    indexes = {{}}
    for item in action.get("selector_metadata") or []:
        selector = item.get("selector")
        if selector and item.get("match_count", 1) > 1:
            indexes[selector] = int(item.get("index", 0))
    return indexes


def ordered_selectors(selectors: list[str]) -> list[str]:
    return sorted(selectors, key=selector_runtime_rank)


def selector_runtime_rank(selector: str) -> tuple[int, str]:
    clean = selector.strip()
    if is_volatile_selector(clean):
        return 90, clean
    if clean.startswith("text="):
        return 10, clean
    if clean.startswith("@aria-label=") or "[aria-label=" in clean:
        return 15, clean
    if clean.startswith("@name=") or "[name=" in clean:
        return 20, clean
    if clean.startswith("@placeholder=") or "[placeholder=" in clean:
        return 25, clean
    if "role=" in clean or "data-orientation=" in clean:
        return 35, clean
    if clean.startswith("css:") and "[type=" in clean:
        return 70, clean
    return 50, clean


def is_volatile_selector(selector: str) -> bool:
    clean = selector.strip()
    return clean.startswith("@id=base-ui-_r_") or clean.startswith("css:#base-ui-_r_") or clean.startswith("@id=_r_") or clean.startswith("css:#_r_")


def selector_lookup_timeout(selector: str, default_timeout: int | float) -> int | float:
    if is_volatile_selector(selector):
        return min(float(default_timeout), DEFAULT_FAST_SELECTOR_TIMEOUT)
    return default_timeout


def configured_action_delay() -> float:
    raw = os.environ.get("ACTION_DELAY_SECONDS")
    if raw is None:
        return float(DEFAULT_ACTION_DELAY_SECONDS or 0)
    try:
        return max(0.0, float(raw))
    except ValueError:
        return float(DEFAULT_ACTION_DELAY_SECONDS or 0)


def preview_click_target(page: ChromiumPage, ele: Any, target: str) -> None:
    delay = configured_action_delay()
    if delay <= 0:
        return
    try:
        page.run_js(
            """
            const el = arguments[0];
            if (!el) return;
            el.scrollIntoView({{block: 'center', inline: 'center'}});
            el.style.outline = '4px solid #ff3b30';
            el.style.outlineOffset = '3px';
            """,
            ele,
        )
        print(f"Target ready: {{target}}")
        page.wait(min(delay, 1.0))
    except Exception:
        return


def find_first(
    page: ChromiumPage,
    selectors: list[str],
    *,
    target: str,
    indexes: dict[str, int] | None = None,
    require_displayed: bool = True,
    timeout: int = DEFAULT_ELEMENT_TIMEOUT,
) -> tuple[Any, str]:
    errors = []
    for selector in ordered_selectors(selectors):
        METRICS["selector_lookup_count"] += 1
        lookup_started = time.perf_counter()
        runtime_selector = normalize_selector(selector)
        index = indexes.get(selector) if indexes and selector in indexes else None
        current_timeout = selector_lookup_timeout(selector, timeout)
        try:
            if require_displayed and index is None:
                ok = page.wait.ele_displayed(runtime_selector, timeout=current_timeout, raise_err=False)
                if not ok:
                    errors.append(f"{{selector}}: not displayed")
                    continue
            if index is None:
                ele = page.ele(runtime_selector, timeout=current_timeout)
            else:
                eles = page.eles(runtime_selector)
                if len(eles) <= index:
                    errors.append(f"{{selector}}: index {{index}} out of range")
                    continue
                ele = eles[index]
                if require_displayed and not is_displayed(ele):
                    errors.append(f"{{selector}}: index {{index}} not displayed")
                    continue
            if ele:
                return ele, selector
        except Exception as exc:
            errors.append(f"{{selector}}: {{type(exc).__name__}}: {{exc}}")
        finally:
            METRICS["selector_lookup_seconds"] += time.perf_counter() - lookup_started
    detail = " | ".join(errors) if errors else "no selectors provided"
    raise RuntimeError(f"Element lookup failed for {{target}}: {{detail}}")


def is_displayed(ele: Any) -> bool:
    if hasattr(ele, "states") and hasattr(ele.states, "is_displayed"):
        value = ele.states.is_displayed
        return bool(value() if callable(value) else value)
    if hasattr(ele, "is_displayed"):
        value = ele.is_displayed
        return bool(value() if callable(value) else value)
    return True


def run_action(page: ChromiumPage, action: dict) -> None:
    action_type = action["type"]
    target = action.get("target") or action_type
    if action_type == "goto":
        page.get(action["url"])
        page.wait.doc_loaded(timeout=DEFAULT_PAGE_TIMEOUT)
        ensure_authenticated(page, target)
        verify_action_postcondition(page, action)
        return
    if action_type == "wait":
        page.wait(float(action.get("seconds") or action.get("value") or 1))
        ensure_authenticated(page, target)
        verify_action_postcondition(page, action)
        return
    if action_type == "press_key":
        key = str(action.get("value") or "").strip()
        if not key:
            raise ValueError(f"Keyboard key is empty for {{target}}")
        press_key(page, key, target)
        ensure_authenticated(page, target)
        verify_action_postcondition(page, action)
        return

    selectors = action.get("fallback_selectors") or []
    indexes = selector_indexes(action)
    if action_type == "click":
        visual_position = action.get("visual_position")
        selector_error = None
        if selectors:
            try:
                ele, _ = find_first(page, selectors, target=target, indexes=indexes)
                preview_click_target(page, ele, target)
                try:
                    ele.click()
                except Exception:
                    page.wait(1)
                    ele, _ = find_first(page, selectors, target=target, indexes=indexes)
                    try:
                        ele.click()
                    except Exception:
                        ele.click(by_js=True)
            except Exception as exc:
                selector_error = exc
        if (not selectors or selector_error is not None) and visual_position:
            if selector_error is not None:
                print(f"Selector replay failed for {{target}}; using audited visual coordinate: {{selector_error}}")
            click_at(page, int(visual_position["x"]), int(visual_position["y"]), target)
        elif selector_error is not None:
            raise selector_error
        elif not selectors:
            ele, _ = find_first(page, selectors, target=target, indexes=indexes)
    elif action_type == "double_click":
        ele, _ = find_first(page, selectors, target=target, indexes=indexes)
        ele.click()
        page.wait(0.1)
        ele.run_js(
            """
            const rect = this.getBoundingClientRect();
            this.dispatchEvent(new MouseEvent('dblclick', {{
              bubbles: true, cancelable: true, view: window, detail: 2,
              clientX: rect.left + rect.width / 2,
              clientY: rect.top + rect.height / 2,
            }}));
            """
        )
    elif action_type == "input":
        ele, _ = find_first(page, selectors, target=target, indexes=indexes)
        if hasattr(ele, "focus"):
            ele.focus()
        value = action.get("value") or ""
        contenteditable = ""
        if hasattr(ele, "attr"):
            try:
                contenteditable = str(ele.attr("contenteditable") or "").lower()
            except Exception:
                contenteditable = ""
        if contenteditable == "true" and hasattr(page, "_run_cdp"):
            dispatch_key_chord(page, "CTRL", "A")
            page._run_cdp("Input.insertText", text=value)
        else:
            ele.input(value, clear=True)
        ele.run_js(
            """
            this.dispatchEvent(new InputEvent('input', {{
              bubbles: true, inputType: 'insertText', data: String(arguments[0])
            }}));
            this.dispatchEvent(new Event('change', {{bubbles: true}}));
            this.blur();
            """,
            value,
        )
        page.wait(0.2)
    elif action_type == "select":
        ele, _ = find_first(page, selectors, target=target, indexes=indexes)
        value = action.get("value") or ""
        select_by = (action.get("select_by") or "text").lower()
        if select_by == "text":
            ele.select.by_text(value)
        elif select_by == "value":
            ele.select.by_value(value)
        elif select_by == "index":
            ele.select.by_index(int(value))
        else:
            raise ValueError(f"Unsupported select mode: {{select_by}}")
    elif action_type == "upload":
        ele, _ = find_first(
            page,
            selectors,
            target=target,
            indexes=indexes,
            require_displayed=False,
        )
        upload_path = Path(action.get("path") or "").expanduser()
        if not upload_path.is_absolute():
            upload_path = BASE_DIR / upload_path
        ele.input(str(upload_path.resolve()))
    elif action_type == "set_range":
        ele, _ = find_first(page, selectors, target=target, indexes=indexes)
        value = str(action.get("value") or "")
        applied = ele.run_js(
            """
            const value = String(arguments[0]);
            this.value = value;
            this.dispatchEvent(new Event('input', {{bubbles: true}}));
            this.dispatchEvent(new Event('change', {{bubbles: true}}));
            return String(this.value);
            """,
            value,
        )
        if str(applied) != value:
            raise RuntimeError(
                f"Range value did not apply for {{target}}: expected {{value!r}}, got {{applied!r}}"
            )
    elif action_type == "set_timecode":
        set_timecode(page, selectors, indexes, str(action.get("value") or ""), target)
    elif action_type == "drag":
        ele, _ = find_first(page, selectors, target=target, indexes=indexes)
        ele.drag(
            offset_x=float(action.get("delta_x") or 0),
            offset_y=float(action.get("delta_y") or 0),
            duration=float(action.get("duration") or 0.5),
        )
    else:
        raise ValueError(f"Unsupported action type: {{action_type}}")
    ensure_authenticated(page, target)
    verify_action_postcondition(page, action)


def verify_action_postcondition(page: ChromiumPage, action: dict) -> None:
    postcondition = action.get("postcondition") or {{}}
    if postcondition.get("type") == "tab_selected":
        label = str(postcondition.get("label") or "").strip().lower()
        selected = page.run_js(
            """
            const expected = String(arguments[0] || '').trim().toLowerCase();
            return Array.from(document.querySelectorAll('[role="tab"]')).some((el) => {{
                const text = String(el.innerText || el.textContent || el.getAttribute('aria-label') || '')
                    .trim()
                    .toLowerCase();
                return text.includes(expected) && (
                    el.getAttribute('aria-selected') === 'true' ||
                    el.getAttribute('data-state') === 'active' ||
                    el.hasAttribute('data-active')
                );
            }});
            """,
            label,
        )
        if not selected:
            raise RuntimeError(f"Postcondition failed: tab {{label!r}} is not selected")
        record_postcondition(action, postcondition)

    state_postcondition = action.get("state_postcondition") or {{}}
    if not state_postcondition:
        return
    postcondition_type = state_postcondition.get("type")
    if postcondition_type == "timeline_media_present":
        if wait_until_timeline_media_present(page):
            record_postcondition(action, state_postcondition)
            return
        raise RuntimeError("State postcondition failed: no media clip was detected in the timeline")
    if postcondition_type in {{"visible_text_contains", "visible_text_all"}}:
        expected_values = [
            str(value).strip().lower()
            for value in (state_postcondition.get("values") or [])
            if str(value).strip()
        ]
        if wait_until_visible_text(page, expected_values, require_all=postcondition_type == "visible_text_all"):
            record_postcondition(action, state_postcondition)
            return
        raise RuntimeError(
            f"State postcondition failed: final text did not satisfy {{postcondition_type}} {{expected_values!r}}"
        )


def record_postcondition(action: dict, postcondition: dict) -> None:
    METRICS["postconditions"].append({{
        "action_type": action.get("type"),
        "target": action.get("target") or action.get("url") or "",
        "type": postcondition.get("type"),
        "passed": True,
        "value": postcondition.get("value"),
        "values": postcondition.get("values"),
    }})


def wait_until_visible_text(page: ChromiumPage, expected_values: list[str], *, require_all: bool) -> bool:
    if not expected_values:
        return True
    for _ in range(5):
        state = page_state_summary(page)
        text = " ".join(str(state.get(key) or "") for key in ["text_excerpt", "title", "url"]).lower()
        if require_all and all(value in text for value in expected_values):
            return True
        if not require_all and any(value in text for value in expected_values):
            return True
        page.wait(1)
    return False


def wait_until_timeline_media_present(page: ChromiumPage) -> bool:
    for _ in range(8):
        present = page.run_js(
            """
            const selectors = [
              '[data-clip]', '[data-track-item]',
              '[class*="timeline"] [class*="clip"]',
              '[class*="timeline"] [class*="fragment"]',
              '[class*="track"] [class*="clip"]',
              '[class*="timeline"] canvas',
              '[class*="track"] canvas'
            ];
            if (selectors.some((selector) => document.querySelector(selector))) return true;
            const text = String(document.body ? document.body.innerText || '' : '').toLowerCase();
            return (
              text.includes('trim') ||
              text.includes('crop') ||
              text.includes('speed') ||
              text.includes('opacity') ||
              text.includes('timeline') ||
              text.includes('export')
            );
            """
        )
        if present:
            return True
        page.wait(1)
    return False


def click_at(page: ChromiumPage, x: int, y: int, target: str) -> None:
    METRICS["visual_coordinate_clicks"] += 1
    clicked = page.run_js(
        """
        const x = arguments[0];
        const y = arguments[1];
        const el = document.elementFromPoint(x, y);
        if (!el) return false;
        el.dispatchEvent(new MouseEvent('mousemove', {{bubbles: true, clientX: x, clientY: y}}));
        el.dispatchEvent(new MouseEvent('mousedown', {{bubbles: true, clientX: x, clientY: y}}));
        el.dispatchEvent(new MouseEvent('mouseup', {{bubbles: true, clientX: x, clientY: y}}));
        el.click();
        return true;
        """,
        x,
        y,
    )
    if not clicked:
        raise RuntimeError(f"Visual click failed for {{target}}: no element at ({{x}}, {{y}})")


def page_state_summary(page: ChromiumPage) -> dict[str, str]:
    try:
        text = page.run_js(
            "return document.body ? String(document.body.innerText || '').slice(0, 2000) : ''"
        ) or ""
    except Exception:
        text = ""
    return {{
        "url": str(getattr(page, "url", "") or ""),
        "title": str(getattr(page, "title", "") or ""),
        "text_excerpt": str(text),
    }}


def write_metrics(page: ChromiumPage | None, *, success: bool, total_seconds: float, error: str | None = None) -> None:
    METRICS["success"] = bool(success)
    METRICS["total_seconds"] = round(float(total_seconds), 4)
    METRICS["pure_execution_seconds"] = round(
        max(0.0, float(total_seconds) - float(METRICS.get("action_delay_seconds") or 0.0)),
        4,
    )
    METRICS["selector_lookup_seconds"] = round(float(METRICS.get("selector_lookup_seconds") or 0.0), 4)
    METRICS["action_delay_seconds"] = round(float(METRICS.get("action_delay_seconds") or 0.0), 4)
    if page is not None:
        METRICS["final_state"] = page_state_summary(page)
    if error:
        METRICS["error"] = error
    METRICS_PATH.write_text(json.dumps(METRICS, ensure_ascii=False, indent=2), encoding="utf-8")


def set_timecode(
    page: ChromiumPage,
    selectors: list[str],
    indexes: dict[str, int],
    value: str,
    target: str,
) -> None:
    match = re.fullmatch(r"(?:(\\d+):)?(\\d{{1,2}}):(\\d{{1,2}})(?:\\.(\\d+))?", value.strip())
    if not match:
        raise ValueError(f"Unsupported timecode value: {{value!r}}")
    expected = {{
        "hours": match.group(1) or "0",
        "minutes": match.group(2),
        "seconds": match.group(3),
        "milliseconds": match.group(4) or "0",
    }}
    for kind in ("hours", "minutes", "seconds", "milliseconds"):
        group, _ = find_first(page, selectors, target=target, indexes=indexes)
        field = group.ele(f"css:.input-{{kind}} .input", timeout=1)
        if not field or not is_displayed(field):
            continue
        current = str(getattr(field, "text", "") or "").strip()
        width = max(1, len(current))
        next_value = str(expected[kind])
        if kind == "milliseconds":
            next_value = next_value[:width].ljust(width, "0")
        else:
            next_value = next_value.zfill(width)
        if current != next_value:
            field.input(next_value)
            page.wait(0.2)


def press_key(page: ChromiumPage, key: str, target: str) -> None:
    normalized = str(key or "").strip().upper()
    chord = normalize_key_chord(normalized)
    if chord and hasattr(page, "_run_cdp"):
        modifier, letter = chord
        dispatch_key_chord(page, modifier, letter)
        return
    specs = {{
        "BACKSPACE": ("Backspace", "Backspace", 8, "deleteBackward"),
        "DELETE": ("Delete", "Delete", 46, "deleteForward"),
        "ENTER": ("Enter", "Enter", 13, None),
        "ESC": ("Escape", "Escape", 27, None),
        "ESCAPE": ("Escape", "Escape", 27, None),
        "ARROWLEFT": ("ArrowLeft", "ArrowLeft", 37, None),
        "ARROWUP": ("ArrowUp", "ArrowUp", 38, None),
        "ARROWRIGHT": ("ArrowRight", "ArrowRight", 39, None),
        "ARROWDOWN": ("ArrowDown", "ArrowDown", 40, None),
    }}
    spec = specs.get(normalized)
    if spec and hasattr(page, "_run_cdp"):
        key_name, code, virtual_key, command = spec
        down = {{
            "type": "rawKeyDown",
            "key": key_name,
            "code": code,
            "windowsVirtualKeyCode": virtual_key,
            "nativeVirtualKeyCode": virtual_key,
        }}
        if command:
            down["commands"] = [command]
        page._run_cdp("Input.dispatchKeyEvent", **down)
        page._run_cdp(
            "Input.dispatchKeyEvent",
            type="keyUp",
            key=key_name,
            code=code,
            windowsVirtualKeyCode=virtual_key,
            nativeVirtualKeyCode=virtual_key,
        )
    else:
        page.actions.key_down(normalized).key_up(normalized)


def normalize_key_chord(normalized: str):
    compact = normalized.replace(" ", "")
    compact = compact.replace("CONTROL+", "CTRL+")
    compact = compact.replace("CMD+", "META+").replace("COMMAND+", "META+")
    if "+" not in compact:
        return None
    modifier, key = compact.split("+", 1)
    if modifier not in {{"CTRL", "META"}}:
        return None
    if len(key) != 1 or not key.isalpha():
        return None
    return modifier, key.upper()


def dispatch_key_chord(page: ChromiumPage, modifier: str, letter: str) -> None:
    modifier_key = "Control" if modifier == "CTRL" else "Meta"
    modifier_code = "ControlLeft" if modifier == "CTRL" else "MetaLeft"
    modifier_vk = 17 if modifier == "CTRL" else 91
    modifier_mask = 2 if modifier == "CTRL" else 4
    letter = letter.upper()
    letter_vk = ord(letter)
    page._run_cdp(
        "Input.dispatchKeyEvent",
        type="rawKeyDown",
        key=modifier_key,
        code=modifier_code,
        windowsVirtualKeyCode=modifier_vk,
        nativeVirtualKeyCode=modifier_vk,
        modifiers=modifier_mask,
    )
    page._run_cdp(
        "Input.dispatchKeyEvent",
        type="rawKeyDown",
        key=letter.lower(),
        code=f"Key{{letter}}",
        windowsVirtualKeyCode=letter_vk,
        nativeVirtualKeyCode=letter_vk,
        modifiers=modifier_mask,
    )
    page._run_cdp(
        "Input.dispatchKeyEvent",
        type="keyUp",
        key=letter.lower(),
        code=f"Key{{letter}}",
        windowsVirtualKeyCode=letter_vk,
        nativeVirtualKeyCode=letter_vk,
        modifiers=modifier_mask,
    )
    page._run_cdp(
        "Input.dispatchKeyEvent",
        type="keyUp",
        key=modifier_key,
        code=modifier_code,
        windowsVirtualKeyCode=modifier_vk,
        nativeVirtualKeyCode=modifier_vk,
    )


def ensure_authenticated(page: ChromiumPage, target: str) -> None:
    wait_enabled = os.environ.get("GENERATED_SCRIPT_WAIT_FOR_LOGIN")
    if wait_enabled is None:
        wait_enabled = "1" if DEFAULT_WAIT_FOR_LOGIN else "0"
    wait_allowed = wait_enabled.lower() in {{"1", "true", "yes", "on"}}
    if is_security_challenge(page):
        if not wait_allowed:
            raise RuntimeError(
                "Browser security verification requires manual completion in headed Chrome."
            )
    elif not is_auth_blocked(page):
        return
    else:
        clicked = try_click_sign_in(page)
        if clicked:
            print(f"Opened sign-in flow automatically using {{clicked}}.")
            page.wait(1)
            if not is_auth_blocked(page) and not is_security_challenge(page):
                return
    if not wait_allowed:
        raise RuntimeError(f"Authentication required while running action {{target}}: {{getattr(page, 'url', '')}}")

    print("Manual authentication or security verification is required. Finish it in the opened browser window; script will continue automatically.")
    deadline = DEFAULT_LOGIN_TIMEOUT_SECONDS
    waited = 0
    while waited < deadline:
        page.wait(2)
        waited += 2
        if waited == 2 or waited % 15 == 0:
            print(f"Waiting for login to complete... {{waited}}s")
        if not is_auth_blocked(page) and not is_security_challenge(page):
            page.wait.doc_loaded(timeout=DEFAULT_PAGE_TIMEOUT)
            print("Login appears complete. Continuing script.")
            return
    raise RuntimeError(f"Timed out waiting for login while running action {{target}}: {{getattr(page, 'url', '')}}")


def is_auth_blocked(page: ChromiumPage) -> bool:
    url = (getattr(page, "url", "") or "").lower()
    title = (getattr(page, "title", "") or "").lower()
    try:
        text = (page.run_js("return document.body ? document.body.innerText : ''") or "").lower()
    except Exception:
        text = ""
    markers = [
        "/login",
        "appleid.apple.com/auth",
        "accounts.google.com",
        "oauth",
        "signin",
        "sign-in",
    ]
    signed_out_markers = [
        "sign in",
        "log in",
        "login",
        "sign in with email",
        "sign in with google",
    ]
    return (
        any(marker in url for marker in markers)
        or "login" in title
        or "sign in" in title
        or any(marker in text for marker in signed_out_markers)
    )


def try_click_sign_in(page: ChromiumPage) -> str | None:
    try:
        return page.run_js(
            """
            const labels = new Set(['sign in', 'signin', 'log in', 'login', '登录']);
            const element = Array.from(document.querySelectorAll('button,a')).find((el) => {{
                const label = String(el.innerText || el.textContent || el.getAttribute('aria-label') || '')
                    .trim()
                    .toLowerCase();
                return labels.has(label);
            }});
            if (!element) return null;
            const label = String(element.innerText || element.textContent || element.getAttribute('aria-label') || '').trim();
            element.click();
            return label;
            """
        )
    except Exception:
        return None


def is_security_challenge(page: ChromiumPage) -> bool:
    try:
        text = (page.run_js("return document.body ? document.body.innerText : ''") or "").lower()
    except Exception:
        text = ""
    markers = [
        "performing security verification",
        "verify you are human",
        "checking your browser",
        "cloudflare",
    ]
    return any(marker in text for marker in markers)


def main() -> int:
    page = build_page()
    total_started = time.perf_counter()
    try:
        for index, action in enumerate(ACTIONS):
            print(f"[{{index + 1}}/{{len(ACTIONS)}}] {{action['type']}}: {{action.get('target') or action.get('url') or ''}}")
            METRICS["current_action"] = action
            action_started = time.perf_counter()
            run_action(page, action)
            action_seconds = time.perf_counter() - action_started
            METRICS["actions"].append({{
                "index": index + 1,
                "type": action.get("type"),
                "target": action.get("target") or action.get("url") or "",
                "seconds": round(action_seconds, 4),
                "used_visual_coordinate": bool(
                    action.get("type") == "click"
                    and action.get("visual_position")
                    and not (action.get("fallback_selectors") or [])
                ),
                "selector_count": len(action.get("fallback_selectors") or []),
            }})
            delay = configured_action_delay()
            if delay > 0:
                print(f"Waiting {{delay:g}}s after action...")
                delay_started = time.perf_counter()
                page.wait(delay)
                METRICS["action_delay_seconds"] += time.perf_counter() - delay_started
        print("Script completed successfully.")
        write_metrics(page, success=True, total_seconds=time.perf_counter() - total_started)
        return 0
    except Exception as exc:
        screenshot_path = BASE_DIR / "outputs" / "generated_script_failure.png"
        screenshot_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            page.get_screenshot(path=str(screenshot_path))
            print(f"Saved failure screenshot: {{screenshot_path}}")
        except Exception:
            pass
        print(f"Script failed: {{type(exc).__name__}}: {{exc}}")
        try:
            write_metrics(
                page,
                success=False,
                total_seconds=time.perf_counter() - total_started,
                error=f"{{type(exc).__name__}}: {{exc}}",
            )
        except Exception:
            pass
        raise
    finally:
        if hasattr(page, "quit"):
            page.quit()
        elif hasattr(page, "close"):
            page.close()


if __name__ == "__main__":
    raise SystemExit(main())
'''

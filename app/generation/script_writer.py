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


def build_page() -> ChromiumPage:
    options = ChromiumOptions()
    browser_path = resolve_browser_path()
    if browser_path and hasattr(options, "set_browser_path"):
        options.set_browser_path(str(browser_path))
    configured_profile = os.environ.get("BROWSER_USER_DATA_PATH") or DEFAULT_BROWSER_USER_DATA_PATH
    profile_path = Path(configured_profile) if configured_profile else Path("outputs") / "generated_script_profile"
    profile_path.mkdir(parents=True, exist_ok=True)
    if hasattr(options, "set_user_data_path"):
        options.set_user_data_path(str(profile_path))
    configured_port = os.environ.get("BROWSER_DEBUG_PORT") or DEFAULT_BROWSER_DEBUG_PORT or "19222"
    if hasattr(options, "set_address"):
        options.set_address(f"127.0.0.1:{{configured_port}}")
    return ChromiumPage(options)


def resolve_browser_path() -> Path | None:
    configured = os.environ.get("BROWSER_PATH")
    if configured and Path(configured).exists():
        return Path(configured)
    for path in [
        r"C:\\soft\\Chrome\\Application\\chrome.exe",
        r"C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
        r"C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe",
        r"C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
        r"C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
    ]:
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
        return
    if action_type == "wait":
        page.wait(float(action.get("seconds") or action.get("value") or 1))
        ensure_authenticated(page, target)
        return

    selectors = action.get("fallback_selectors") or []
    indexes = selector_indexes(action)
    if action_type == "click":
        visual_position = action.get("visual_position")
        if visual_position:
            click_at(page, int(visual_position["x"]), int(visual_position["y"]), target)
        else:
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
    elif action_type == "input":
        ele, _ = find_first(page, selectors, target=target, indexes=indexes)
        ele.input(action.get("value") or "")
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
        ele.input(str(Path(action.get("path") or "")))
    else:
        raise ValueError(f"Unsupported action type: {{action_type}}")
    ensure_authenticated(page, target)
    verify_action_postcondition(page, action)


def verify_action_postcondition(page: ChromiumPage, action: dict) -> None:
    postcondition = action.get("postcondition") or {{}}
    if postcondition.get("type") != "tab_selected":
        return
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


def click_at(page: ChromiumPage, x: int, y: int, target: str) -> None:
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


def ensure_authenticated(page: ChromiumPage, target: str) -> None:
    if not is_auth_blocked(page):
        return
    wait_enabled = os.environ.get("GENERATED_SCRIPT_WAIT_FOR_LOGIN")
    if wait_enabled is None:
        wait_enabled = "1" if DEFAULT_WAIT_FOR_LOGIN else "0"
    if wait_enabled.lower() not in {{"1", "true", "yes", "on"}}:
        raise RuntimeError(f"Authentication required while running action {{target}}: {{getattr(page, 'url', '')}}")

    print("Authentication required. Finish login in the opened browser window; script will continue automatically.")
    deadline = DEFAULT_LOGIN_TIMEOUT_SECONDS
    waited = 0
    while waited < deadline:
        page.wait(2)
        waited += 2
        if waited == 2 or waited % 15 == 0:
            print(f"Waiting for login to complete... {{waited}}s")
        if not is_auth_blocked(page):
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


def main() -> int:
    page = build_page()
    try:
        for index, action in enumerate(ACTIONS):
            print(f"[{{index + 1}}/{{len(ACTIONS)}}] {{action['type']}}: {{action.get('target') or action.get('url') or ''}}")
            run_action(page, action)
            delay = configured_action_delay()
            if delay > 0:
                print(f"Waiting {{delay:g}}s after action...")
                page.wait(delay)
        print("Script completed successfully.")
        return 0
    except Exception as exc:
        screenshot_path = Path("outputs") / "generated_script_failure.png"
        try:
            page.get_screenshot(path=str(screenshot_path))
            print(f"Saved failure screenshot: {{screenshot_path}}")
        except Exception:
            pass
        print(f"Script failed: {{type(exc).__name__}}: {{exc}}")
        raise
    finally:
        if hasattr(page, "quit"):
            page.quit()
        elif hasattr(page, "close"):
            page.close()


if __name__ == "__main__":
    raise SystemExit(main())
'''

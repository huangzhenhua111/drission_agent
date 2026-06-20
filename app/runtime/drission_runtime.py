from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from app.config import Settings, load_settings
from app.generation.dom_snapshot import extract_candidates_from_html
from app.resilience.selectors import build_selector_candidates, normalize_text
from app.resilience.waits import DEFAULT_ELEMENT_TIMEOUT, DEFAULT_PAGE_TIMEOUT


_WORD_RE = re.compile(r"[a-z0-9_]+")
_ITEM_ACTION_WORDS = {
    "select",
    "choose",
    "pick",
    "add",
    "insert",
    "use",
    "reuse",
    "apply",
    "confirm",
    "download",
    "share",
}
_ITEM_ACTION_CJK_WORDS = [
    "选",
    "选择",
    "添加",
    "加入",
    "使用",
    "复用",
    "采用",
    "确认",
    "下载",
    "分享",
]


class DrissionRuntime:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or load_settings()
        self.page: Any | None = None

    def start(self) -> None:
        if self.page is not None:
            return

        try:
            from DrissionPage import ChromiumOptions, ChromiumPage
        except ModuleNotFoundError as exc:
            raise RuntimeError(
                "DrissionPage is not installed. Run: pip install -r requirements.txt"
            ) from exc

        browser_path = self._resolve_browser_path()
        profile_path = self._resolve_user_data_path(browser_path)
        address = f"127.0.0.1:{self.settings.browser_debug_port}"
        if browser_path:
            options = ChromiumOptions()
            if hasattr(options, "set_browser_path"):
                options.set_browser_path(str(browser_path))
            if profile_path and hasattr(options, "set_user_data_path"):
                options.set_user_data_path(str(profile_path))
            if hasattr(options, "set_address"):
                options.set_address(address)
            self.page = ChromiumPage(options)
            return

        options = ChromiumOptions()
        if profile_path and hasattr(options, "set_user_data_path"):
            options.set_user_data_path(str(profile_path))
        if hasattr(options, "set_address"):
            options.set_address(address)
        self.page = ChromiumPage(options)

    def goto(self, url: str) -> None:
        page = self._require_page()
        page.get(url)
        page.wait.doc_loaded(timeout=DEFAULT_PAGE_TIMEOUT)

    def snapshot(self) -> list[dict]:
        page = self._require_page()
        try:
            return self._snapshot_from_browser()
        except Exception:
            return [candidate.to_dict() for candidate in extract_candidates_from_html(page.html)]

    def click(
        self,
        selectors: list[str],
        target: str,
        *,
        selector_indexes: dict[str, int] | None = None,
    ) -> str:
        ele, used_selector = self.find_first(
            selectors, target=target, selector_indexes=selector_indexes
        )
        try:
            ele.click()
        except Exception:
            self.wait(1)
            ele, used_selector = self.find_first(
                selectors, target=target, selector_indexes=selector_indexes
            )
            try:
                ele.click()
            except Exception:
                ele.click(by_js=True)
        return used_selector

    def click_at(self, x: int, y: int, target: str) -> str:
        page = self._require_page()
        clicked = page.run_js(
            """
            const x = arguments[0];
            const y = arguments[1];
            const el = document.elementFromPoint(x, y);
            if (!el) return false;
            el.dispatchEvent(new MouseEvent('mousemove', {bubbles: true, clientX: x, clientY: y}));
            el.dispatchEvent(new MouseEvent('mousedown', {bubbles: true, clientX: x, clientY: y}));
            el.dispatchEvent(new MouseEvent('mouseup', {bubbles: true, clientX: x, clientY: y}));
            el.click();
            return true;
            """,
            x,
            y,
        )
        if not clicked:
            raise RuntimeError(f"Visual click failed for {target}: no element at ({x}, {y})")
        return f"visual:{x},{y}"

    def input(
        self,
        selectors: list[str],
        value: str,
        target: str,
        *,
        selector_indexes: dict[str, int] | None = None,
    ) -> str:
        ele, used_selector = self.find_first(
            selectors, target=target, selector_indexes=selector_indexes
        )
        ele.input(value)
        return used_selector

    def select(
        self,
        selectors: list[str],
        value: str,
        by: str,
        target: str,
        *,
        selector_indexes: dict[str, int] | None = None,
    ) -> str:
        ele, used_selector = self.find_first(
            selectors, target=target, selector_indexes=selector_indexes
        )
        normalized_by = by.lower()
        if normalized_by == "text":
            ele.select.by_text(value)
        elif normalized_by == "value":
            ele.select.by_value(value)
        elif normalized_by == "index":
            ele.select.by_index(int(value))
        else:
            raise ValueError(f"Unsupported select mode: {by}")
        return used_selector

    def upload(
        self,
        selectors: list[str],
        path: str,
        target: str,
        *,
        selector_indexes: dict[str, int] | None = None,
    ) -> str:
        ele, used_selector = self.find_first(
            selectors,
            target=target,
            require_displayed=False,
            selector_indexes=selector_indexes,
        )
        ele.input(str(Path(path)))
        return used_selector

    def wait(self, seconds: float) -> None:
        page = self._require_page()
        page.wait(seconds)

    def state(self) -> dict:
        page = self._require_page()
        html = ""
        text = ""
        for attempt in range(3):
            try:
                html = page.html or ""
                text = page.run_js("return document.body ? document.body.innerText : ''") or ""
                break
            except Exception:
                if attempt == 2:
                    break
                self.wait(0.5)
        return {
            "url": getattr(page, "url", ""),
            "title": getattr(page, "title", ""),
            "html_excerpt": html[:4000],
            "text_excerpt": str(text)[:4000],
        }

    def screenshot(self, path: str) -> None:
        page = self._require_page()
        output = Path(path)
        output.parent.mkdir(parents=True, exist_ok=True)
        try:
            page.get_screenshot(path=str(output))
        except TypeError:
            page.get_screenshot(str(output))

    def browser_profile_path(self) -> Path:
        return self._resolve_user_data_path(self._resolve_browser_path())

    def close(self) -> None:
        if self.page is None:
            return
        if hasattr(self.page, "quit"):
            self.page.quit()
        elif hasattr(self.page, "close"):
            self.page.close()
        self.page = None

    def find_first(
        self,
        selectors: list[str],
        *,
        target: str,
        timeout: int = DEFAULT_ELEMENT_TIMEOUT,
        index: int | None = None,
        selector_indexes: dict[str, int] | None = None,
        require_displayed: bool = True,
    ) -> tuple[Any, str]:
        page = self._require_page()
        errors: list[str] = []

        for selector in selectors:
            runtime_selector = normalize_runtime_selector(selector)
            selector_index = (
                selector_indexes.get(selector)
                if selector_indexes and selector in selector_indexes
                else index
            )
            try:
                if require_displayed and selector_index is None:
                    ok = page.wait.ele_displayed(
                        runtime_selector, timeout=timeout, raise_err=False
                    )
                    if not ok:
                        errors.append(f"{selector}: not displayed")
                        continue

                if selector_index is None:
                    ele = page.ele(runtime_selector, timeout=timeout)
                else:
                    eles = page.eles(runtime_selector)
                    if len(eles) <= selector_index:
                        errors.append(f"{selector}: index {selector_index} out of range")
                        continue
                    ele = eles[selector_index]
                    if require_displayed and not _element_is_displayed(ele):
                        errors.append(f"{selector}: index {selector_index} not displayed")
                        continue

                if ele:
                    return ele, selector
            except Exception as exc:
                errors.append(f"{selector}: {type(exc).__name__}: {exc}")

        detail = " | ".join(errors) if errors else "no selectors provided"
        raise RuntimeError(f"Element lookup failed for {target}: {detail}")

    def _require_page(self) -> Any:
        if self.page is None:
            self.start()
        if self.page is None:
            raise RuntimeError("Failed to start DrissionPage runtime.")
        return self.page

    def _resolve_browser_path(self) -> Path | None:
        if self.settings.browser_path:
            configured = Path(self.settings.browser_path)
            if configured.exists():
                return configured

        candidates = [
            Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
            Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
            Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
            Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
        ]
        for candidate in candidates:
            if candidate.exists():
                return candidate
        return None

    def _resolve_user_data_path(self, browser_path: Path | None) -> Path:
        if self.settings.browser_user_data_path:
            path = self.settings.browser_user_data_path
        else:
            browser_name = browser_path.stem if browser_path else self.settings.browser_type
            path = self.settings.output_dir / "browser_profiles" / browser_name
        path.mkdir(parents=True, exist_ok=True)
        return path.resolve()

    def _snapshot_from_browser(self) -> list[dict]:
        page = self._require_page()
        raw_candidates = page.run_js(_BROWSER_CANDIDATE_JS)
        candidates: list[dict] = []
        for index, raw in enumerate(raw_candidates, start=1):
            data_attrs = raw.get("data_attrs") or {}
            text = normalize_text(raw.get("text"))
            context_chain = raw.get("context_chain") or []
            primary_context_text = None
            if context_chain and isinstance(context_chain[0], dict):
                primary_context_text = normalize_text(context_chain[0].get("text"), max_length=160)
            semantic_type = _semantic_type(raw)
            action_allowed = _action_allowed(raw, semantic_type)
            selectors = build_selector_candidates(
                tag=raw.get("tag") or "",
                text=text,
                id_value=raw.get("id"),
                name=raw.get("name"),
                type_value=raw.get("type"),
                role=raw.get("role"),
                aria_label=raw.get("aria_label"),
                placeholder=raw.get("placeholder"),
                value=raw.get("value"),
                data_attrs=data_attrs,
            )
            if not selectors:
                continue
            candidates.append(
                {
                    "candidate_id": f"e{index}",
                    "dom_index": raw.get("dom_index"),
                    "tag": raw.get("tag"),
                    "semantic_type": semantic_type,
                    "action_allowed": action_allowed,
                    "text": text,
                    "id": raw.get("id"),
                    "name": raw.get("name"),
                    "type": raw.get("type"),
                    "role": raw.get("role"),
                    "aria_label": raw.get("aria_label"),
                    "aria_selected": raw.get("aria_selected"),
                    "data_state": raw.get("data_state"),
                    "placeholder": raw.get("placeholder"),
                    "value": raw.get("value"),
                    "href": raw.get("href"),
                    "accept": raw.get("accept"),
                    "multiple": bool(raw.get("multiple")),
                    "contenteditable": raw.get("contenteditable"),
                    "options": raw.get("options") or [],
                    "selected": raw.get("selected"),
                    "data_attrs": data_attrs,
                    "is_visible": bool(raw.get("is_visible")),
                    "rect": raw.get("rect"),
                    "css_path": raw.get("css_path"),
                    "accessible_name": normalize_text(raw.get("accessible_name"), max_length=160),
                    "label_text": normalize_text(raw.get("label_text"), max_length=160),
                    "primary_context_text": primary_context_text,
                    "context_text": normalize_text(raw.get("context_text"), max_length=180),
                    "upload_label": normalize_text(raw.get("upload_label"), max_length=80),
                    "upload_kind": raw.get("upload_kind"),
                    "nearest_card_text": normalize_text(raw.get("nearest_card_text"), max_length=180),
                    "ancestor_text": normalize_text(raw.get("ancestor_text"), max_length=160),
                    "context_chain": context_chain,
                    "selector_candidates": selectors,
                }
            )
        _annotate_clickable_item_ranks(candidates)
        _annotate_selector_occurrences(candidates)
        return candidates


def normalize_runtime_selector(selector: str) -> str:
    clean = selector.strip()
    if clean.startswith("text="):
        return "text:" + clean[len("text=") :]
    return clean


def _element_is_displayed(ele: Any) -> bool:
    if hasattr(ele, "states") and hasattr(ele.states, "is_displayed"):
        value = ele.states.is_displayed
        return bool(value() if callable(value) else value)
    if hasattr(ele, "is_displayed"):
        value = ele.is_displayed
        return bool(value() if callable(value) else value)
    return True


def _annotate_selector_occurrences(candidates: list[dict]) -> None:
    counts: dict[str, int] = {}
    for candidate in candidates:
        indexes: dict[str, int] = {}
        for selector in candidate.get("selector_candidates", []):
            current = counts.get(selector, 0)
            indexes[selector] = current
            counts[selector] = current + 1
        candidate["selector_indexes"] = indexes

    for candidate in candidates:
        candidate["selector_match_counts"] = {
            selector: counts.get(selector, 0)
            for selector in candidate.get("selector_candidates", [])
        }
        candidate["selector_metadata"] = [
            {
                "selector": selector,
                "index": candidate["selector_indexes"].get(selector, 0),
                "match_count": counts.get(selector, 0),
                "unique": counts.get(selector, 0) == 1,
            }
            for selector in candidate.get("selector_candidates", [])
        ]


def _annotate_clickable_item_ranks(candidates: list[dict]) -> None:
    items = [
        candidate
        for candidate in candidates
        if candidate.get("semantic_type") == "clickable_item" and candidate.get("is_visible")
    ]
    items.sort(
        key=lambda candidate: (
            int((candidate.get("rect") or {}).get("y") or 0),
            int((candidate.get("rect") or {}).get("x") or 0),
            int(candidate.get("dom_index") or 0),
        )
    )
    for rank, candidate in enumerate(items, start=1):
        candidate["result_rank"] = rank
    _annotate_related_item_actions(candidates, items)


def _annotate_related_item_actions(candidates: list[dict], ranked_items: list[dict]) -> None:
    for candidate in candidates:
        if candidate.get("semantic_type") == "clickable_item" or not candidate.get("is_visible"):
            continue
        if not _is_item_action_control(candidate):
            continue
        related = _containing_ranked_item(candidate, ranked_items)
        if not related:
            continue
        candidate["related_item_rank"] = related.get("result_rank")
        candidate["related_item_candidate_id"] = related.get("candidate_id")
        related_context = (
            related.get("context_text")
            or related.get("accessible_name")
            or related.get("text")
            or related.get("aria_label")
        )
        if related_context:
            candidate["related_item_context"] = normalize_text(related_context, max_length=160)


def _is_item_action_control(candidate: dict) -> bool:
    tag = (candidate.get("tag") or "").lower()
    role = (candidate.get("role") or "").lower()
    type_value = (candidate.get("type") or "").lower()
    is_control = tag in {"button", "a"} or role in {"button", "link"} or (
        tag == "input" and type_value in {"button", "submit", "reset"}
    )
    if not is_control:
        return False
    label = " ".join(
        str(candidate.get(key) or "")
        for key in ["text", "accessible_name", "aria_label", "label_text", "context_text"]
    ).lower()
    tokens = set(_WORD_RE.findall(label))
    return bool(tokens & _ITEM_ACTION_WORDS) or any(word in label for word in _ITEM_ACTION_CJK_WORDS)


def _containing_ranked_item(candidate: dict, ranked_items: list[dict]) -> dict | None:
    rect = candidate.get("rect") or {}
    center_x = float(rect.get("x") or 0) + float(rect.get("width") or 0) / 2
    center_y = float(rect.get("y") or 0) + float(rect.get("height") or 0) / 2
    containing: list[tuple[float, int, dict]] = []
    for item in ranked_items:
        if item.get("candidate_id") == candidate.get("candidate_id"):
            continue
        item_rect = item.get("rect") or {}
        x = float(item_rect.get("x") or 0)
        y = float(item_rect.get("y") or 0)
        width = float(item_rect.get("width") or 0)
        height = float(item_rect.get("height") or 0)
        if width <= 0 or height <= 0:
            continue
        margin = 3.0
        if x - margin <= center_x <= x + width + margin and y - margin <= center_y <= y + height + margin:
            containing.append((width * height, int(item.get("dom_index") or 0), item))
    if not containing:
        return None
    containing.sort(key=lambda item: (item[0], item[1]))
    return containing[0][2]


def _semantic_type(raw: dict) -> str:
    tag = (raw.get("tag") or "").lower()
    type_value = (raw.get("type") or "").lower()
    role = (raw.get("role") or "").lower()
    text_blob = " ".join(
        str(value or "")
        for value in [
            raw.get("text"),
            raw.get("accessible_name"),
            raw.get("primary_context_text"),
            raw.get("context_text"),
            raw.get("upload_label"),
            raw.get("nearest_card_text"),
        ]
    ).lower()
    if tag == "input" and type_value == "file":
        return "file_input"
    own_labels = {
        str(value or "").strip().lower()
        for value in [raw.get("text"), raw.get("accessible_name"), raw.get("aria_label")]
        if str(value or "").strip()
    }
    if tag == "button" and "library" in own_labels and raw.get("upload_label"):
        return "library_button"
    if role == "button" and ("upload" in text_blob or "drag and drop" in text_blob):
        return "upload_zone"
    if _is_clickable_item(raw, text_blob):
        return "clickable_item"
    if tag == "select":
        return "select"
    if tag == "textarea":
        return "textarea"
    if tag == "input":
        return f"input_{type_value or 'text'}"
    if tag == "button" or role == "button":
        return "button"
    if tag == "a":
        return "link"
    return tag or "element"


def _action_allowed(raw: dict, semantic_type: str) -> list[str]:
    tag = (raw.get("tag") or "").lower()
    type_value = (raw.get("type") or "").lower()
    role = (raw.get("role") or "").lower()
    if semantic_type == "file_input":
        return ["upload"]
    if semantic_type == "upload_zone":
        return ["click", "click_upload_zone"]
    if semantic_type == "library_button":
        return ["click"]
    if semantic_type == "clickable_item":
        return ["click"]
    if semantic_type == "select":
        return ["select", "click"]
    if tag == "input" and type_value in {"text", "search", "password", "email", "number", "date", "range", "color", ""}:
        return ["input", "click"]
    if tag == "textarea":
        return ["input", "click"]
    if tag == "button" or tag == "a" or role == "button" or type_value in {"button", "submit", "checkbox", "radio"}:
        return ["click"]
    return ["inspect"]


def _is_clickable_item(raw: dict, text_blob: str) -> bool:
    role = (raw.get("role") or "").lower()
    tag = (raw.get("tag") or "").lower()
    type_value = (raw.get("type") or "").lower()
    rect = raw.get("rect") or {}
    width = int(rect.get("width") or 0)
    height = int(rect.get("height") or 0)
    if role not in {"button", "link"} and tag not in {"a", "button"}:
        return False
    if type_value in {"submit", "reset", "checkbox", "radio"}:
        return False
    if width < 48 or height < 48:
        return False
    command_words = [
        "submit",
        "cancel",
        "close",
        "download",
        "share",
        "delete",
        "reuse",
        "sign in",
        "log in",
        "upgrade",
    ]
    if any(word in text_blob for word in command_words):
        return False
    return True


_BROWSER_CANDIDATE_JS = r"""
return (() => {
  const interactiveTags = new Set(['a', 'button', 'input', 'textarea', 'select', 'option', 'label']);
  const interactiveAttrs = ['role', 'onclick', 'tabindex'];
  const skippedTags = new Set(['script', 'style', 'noscript', 'template', 'meta', 'link']);

  function attr(el, name) {
    const value = el.getAttribute(name);
    return value === null ? null : value;
  }

  function textOf(el) {
    const text = el.innerText || el.textContent || '';
    return text.replace(/\s+/g, ' ').trim();
  }

  function dataAttrs(el) {
    const result = {};
    for (const item of el.attributes) {
      if (item.name.startsWith('data-')) {
        result[item.name] = item.value;
      }
    }
    return result;
  }

  function isCandidate(el) {
    const tag = el.tagName.toLowerCase();
    if (skippedTags.has(tag)) return false;
    if (interactiveTags.has(tag)) return true;
    if (interactiveAttrs.some(name => el.hasAttribute(name))) return true;
    for (const item of el.attributes) {
      if (item.name.startsWith('data-')) return true;
    }
    return false;
  }

  function isVisible(el) {
    const tag = el.tagName.toLowerCase();
    if (el.hidden) return false;
    if (tag === 'input' && (attr(el, 'type') || '').toLowerCase() === 'hidden') return false;
    const style = window.getComputedStyle(el);
    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
    const rect = el.getBoundingClientRect();
    return !!(rect.width || rect.height || el.getClientRects().length);
  }

  function rectOf(el) {
    const rect = el.getBoundingClientRect();
    return {
      x: Math.round(rect.x),
      y: Math.round(rect.y),
      width: Math.round(rect.width),
      height: Math.round(rect.height)
    };
  }

  function cssPath(el) {
    const parts = [];
    let current = el;
    while (current && current.nodeType === Node.ELEMENT_NODE && parts.length < 6) {
      const tag = current.tagName.toLowerCase();
      const id = current.getAttribute('id');
      if (id) {
        parts.unshift(`${tag}#${CSS.escape(id)}`);
        break;
      }
      let index = 1;
      let previous = current.previousElementSibling;
      while (previous) {
        if (previous.tagName.toLowerCase() === tag) index += 1;
        previous = previous.previousElementSibling;
      }
      parts.unshift(`${tag}:nth-of-type(${index})`);
      current = current.parentElement;
    }
    return parts.join(' > ');
  }

  function ancestorText(el) {
    let current = el.parentElement;
    while (current && current !== document.body) {
      const role = current.getAttribute('role');
      const tag = current.tagName.toLowerCase();
      if (role === 'button' || tag === 'button' || tag === 'label' || current.hasAttribute('aria-label')) {
        const text = textOf(current) || current.getAttribute('aria-label') || '';
        if (text) return text;
      }
      current = current.parentElement;
    }
    return '';
  }

  function labelText(el) {
    const id = el.getAttribute('id');
    if (id) {
      const label = document.querySelector(`label[for="${CSS.escape(id)}"]`);
      if (label) {
        const text = textOf(label);
        if (text) return text;
      }
    }
    const closestLabel = el.closest('label');
    if (closestLabel) {
      const text = textOf(closestLabel);
      if (text) return text;
    }
    return '';
  }

  function accessibleName(el) {
    const aria = el.getAttribute('aria-label');
    if (aria) return aria;
    const labelledBy = el.getAttribute('aria-labelledby');
    if (labelledBy) {
      const text = labelledBy
        .split(/\s+/)
        .map(id => document.getElementById(id))
        .filter(Boolean)
        .map(node => textOf(node))
        .filter(Boolean)
        .join(' ');
      if (text) return text;
    }
    const label = labelText(el);
    if (label) return label;
    return textOf(el);
  }

  function compactText(value, maxLength = 220) {
    const text = (value || '').replace(/\s+/g, ' ').trim();
    return text.length > maxLength ? text.slice(0, maxLength).trim() : text;
  }

  function contextText(el) {
    const pieces = [
      labelText(el),
      accessibleName(el),
      nearestCardText(el),
      ancestorText(el)
    ].filter(Boolean);
    return compactText(Array.from(new Set(pieces)).join(' | '), 220);
  }

  function nearestCardElement(el) {
    const text = textOf(el);
    if (text && text.length > 4) return el;

    let current = el.parentElement;
    let depth = 0;
    while (current && current !== document.body && depth < 5) {
      const rect = current.getBoundingClientRect();
      const currentText = compactText(textOf(current), 180);
      if (
        currentText &&
        rect.width > 20 &&
        rect.height > 20 &&
        currentText.length < 220
      ) {
        return current;
      }
      current = current.parentElement;
      depth += 1;
    }
    return el.parentElement || el;
  }

  function nearestCardText(el) {
    const card = nearestCardElement(el);
    return card ? compactText(textOf(card), 180) : '';
  }

  function uploadLabel(el) {
    const cardText = `${nearestCardText(el)} ${ancestorText(el)}`;
    const match = cardText.match(/Add\s+(Motion|Image|Video|Audio|File)/i);
    if (match) return `Add ${match[1]}`;
    if (/motion/i.test(cardText)) return 'Add Motion';
    if (/image/i.test(cardText)) return 'Add Image';
    if (/video/i.test(cardText)) return 'Add Video';
    return '';
  }

  function uploadKind(el) {
    const text = `${uploadLabel(el)} ${nearestCardText(el)} ${ancestorText(el)}`.toLowerCase();
    if (text.includes('motion') || text.includes('video')) return 'video';
    if (text.includes('image') || text.includes('photo') || text.includes('character')) return 'image';
    return '';
  }

  function contextChain(el) {
    const chain = [];
    const seen = new Set();
    let current = el.parentElement;
    let depth = 0;
    while (current && current !== document.body && depth < 5) {
      const text = compactText(textOf(current), 120);
      const key = `${current.tagName.toLowerCase()}|${current.getAttribute('role') || ''}|${current.getAttribute('aria-label') || ''}|${text}`;
      if (seen.has(key)) {
        current = current.parentElement;
        depth += 1;
        continue;
      }
      seen.add(key);
      const item = {
        tag: current.tagName.toLowerCase(),
        role: current.getAttribute('role'),
        aria_label: current.getAttribute('aria-label'),
        text,
        rect: rectOf(current)
      };
      if (item.role || item.aria_label || item.text) chain.push(item);
      current = current.parentElement;
      depth += 1;
    }
    return chain;
  }

  return Array.from(document.querySelectorAll('*'))
    .map((el, index) => ({ el, index }))
    .filter(({ el }) => isCandidate(el))
    .map(({ el, index }) => {
      const tag = el.tagName.toLowerCase();
      return {
        dom_index: index,
        tag,
        text: textOf(el),
        id: attr(el, 'id'),
        name: attr(el, 'name'),
        type: attr(el, 'type'),
        role: attr(el, 'role'),
        aria_label: attr(el, 'aria-label'),
        aria_selected: attr(el, 'aria-selected'),
        data_state: attr(el, 'data-state'),
        placeholder: attr(el, 'placeholder'),
        value: attr(el, 'value'),
        href: attr(el, 'href'),
        accept: attr(el, 'accept'),
        multiple: el.hasAttribute('multiple'),
        contenteditable: attr(el, 'contenteditable'),
        options: tag === 'select' ? Array.from(el.options || []).map(option => ({
          text: textOf(option),
          value: option.value,
          selected: option.selected
        })) : [],
        selected: tag === 'select' && el.selectedOptions && el.selectedOptions.length
          ? textOf(el.selectedOptions[0]) || el.selectedOptions[0].value
          : null,
        data_attrs: dataAttrs(el),
        is_visible: isVisible(el),
        rect: rectOf(el),
        css_path: cssPath(el),
        accessible_name: accessibleName(el),
        label_text: labelText(el),
        upload_label: uploadLabel(el),
        upload_kind: uploadKind(el),
        nearest_card_text: nearestCardText(el),
        context_text: contextText(el),
        ancestor_text: ancestorText(el),
        context_chain: contextChain(el)
      };
    });
})();
"""


def main() -> int:
    runtime = DrissionRuntime()
    runtime.goto("https://example.com")
    try:
        print(runtime.state()["title"])
        return 0
    finally:
        runtime.close()


if __name__ == "__main__":
    raise SystemExit(main())

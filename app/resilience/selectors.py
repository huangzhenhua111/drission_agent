from __future__ import annotations

import re


SELECTOR_PRIORITY = [
    "id",
    "name",
    "aria-label",
    "data",
    "placeholder",
    "role-text",
    "type-text",
    "text",
    "css",
    "xpath",
]


_UNSAFE_CSS_CHARS = re.compile(r"([\\'\"\[\]#.:>+~,*=])")
_SPACE_RE = re.compile(r"\s+")
_VOLATILE_ID_RE = re.compile(r"^(?:base-ui-)?_r_[a-z0-9]+_$", re.IGNORECASE)


def normalize_text(text: str | None, max_length: int = 80) -> str | None:
    if not text:
        return None
    normalized = _SPACE_RE.sub(" ", text).strip()
    if not normalized:
        return None
    if len(normalized) > max_length:
        return normalized[:max_length].rstrip()
    return normalized


def css_quote(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def css_escape_identifier(value: str) -> str:
    return _UNSAFE_CSS_CHARS.sub(r"\\\1", value)


def is_stable_data_attr(name: str) -> bool:
    lowered = name.lower()
    return lowered in {"data-testid", "data-test", "data-qa", "data-cy"} or lowered.startswith(
        "data-"
    )


def is_volatile_id(value: str | None) -> bool:
    if not value:
        return False
    return bool(_VOLATILE_ID_RE.match(value.strip()))



def is_overbroad_selector(selector: str) -> bool:
    normalized = selector.strip().lower()
    return normalized in {
        "css:a",
        "css:button",
        "css:input",
        "css:select",
        "css:textarea",
        "a",
        "button",
        "input",
        "select",
        "textarea",
    }


def dedupe_selectors(selectors: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for selector in selectors:
        clean = selector.strip()
        if not clean or clean in seen:
            continue
        if is_fragile_xpath(clean):
            continue
        seen.add(clean)
        result.append(clean)
    return result


def build_selector_candidates(
    *,
    tag: str,
    text: str | None = None,
    id_value: str | None = None,
    name: str | None = None,
    type_value: str | None = None,
    role: str | None = None,
    aria_label: str | None = None,
    placeholder: str | None = None,
    value: str | None = None,
    data_attrs: dict[str, str] | None = None,
) -> list[str]:
    """Return fallback selectors ordered from stable to broad.

    The strings use a small internal convention accepted by DrissionRuntime:
    DrissionPage-native `@attr=value`, `css:...`, and `text=...`.
    """

    selectors: list[str] = []
    data_attrs = data_attrs or {}
    visible_text = normalize_text(text)
    tag = tag.lower()

    volatile_id_selectors: list[str] = []
    if id_value:
        id_selectors = [f"@id={id_value}", f"css:#{css_escape_identifier(id_value)}"]
        if is_volatile_id(id_value):
            volatile_id_selectors.extend(id_selectors)
        else:
            selectors.extend(id_selectors)

    if name:
        selectors.append(f"@name={name}")
        selectors.append(f"css:{tag}[name={css_quote(name)}]")

    if aria_label:
        selectors.append(f"@aria-label={aria_label}")
        selectors.append(f"css:{tag}[aria-label={css_quote(aria_label)}]")

    for attr_name in sorted(data_attrs):
        attr_value = data_attrs[attr_name]
        if not attr_value or not is_stable_data_attr(attr_name):
            continue
        selectors.append(f"css:{tag}[{attr_name}={css_quote(attr_value)}]")
        selectors.append(f"css:[{attr_name}={css_quote(attr_value)}]")

    if placeholder:
        selectors.append(f"@placeholder={placeholder}")
        selectors.append(f"css:{tag}[placeholder={css_quote(placeholder)}]")

    if role:
        selectors.append(f"css:{tag}[role={css_quote(role)}]")
        selectors.append(f"css:[role={css_quote(role)}]")

    if type_value:
        selectors.append(f"css:{tag}[type={css_quote(type_value)}]")

    if value and tag in {"input", "option", "button"}:
        selectors.append(f"css:{tag}[value={css_quote(value)}]")

    if visible_text and tag in {"a", "button", "label", "option"}:
        selectors.append(f"text={visible_text}")

    if tag in {"button", "input"} and type_value and visible_text:
        selectors.append(f"css:{tag}[type={css_quote(type_value)}]")

    selectors.extend(volatile_id_selectors)
    return dedupe_selectors([selector for selector in selectors if not is_overbroad_selector(selector)])


def is_fragile_xpath(selector: str) -> bool:
    normalized = selector.strip()
    return normalized.startswith("/html/") or normalized.startswith("xpath:/html/")

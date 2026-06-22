from __future__ import annotations

import re
from copy import deepcopy


_WORD_RE = re.compile(r"[a-z0-9_]+")
_ACTION_WORDS = {
    "select",
    "choose",
    "pick",
    "add",
    "insert",
    "use",
    "reuse",
    "apply",
    "confirm",
}
_ACTION_CJK_WORDS = ["选", "选择", "添加", "加入", "使用", "复用", "采用", "确认"]

COMMON_FIELDS = [
    "candidate_id",
    "dom_index",
    "tag",
    "semantic_type",
    "action_allowed",
    "text",
    "id",
    "name",
    "type",
    "role",
    "aria_label",
    "aria_selected",
    "data_state",
    "data_attrs",
    "is_visible",
    "rect",
    "selector_candidates",
    "selector_metadata",
]

ACTION_FIELDS = {
    "click": [
        "href",
        "accessible_name",
        "label_text",
        "context_text",
        "upload_label",
        "upload_kind",
        "ancestor_text",
        "result_rank",
        "related_item_rank",
        "related_item_candidate_id",
        "related_item_context",
    ],
    "double_click": [
        "href", "accessible_name", "label_text", "context_text",
        "ancestor_text", "result_rank",
    ],
    "input": [
        "placeholder",
        "value",
        "accessible_name",
        "label_text",
        "context_text",
        "current_value",
    ],
    "select": [
        "value",
        "accessible_name",
        "label_text",
        "context_text",
        "options",
        "selected",
    ],
    "upload": [
        "accept",
        "multiple",
        "context_text",
        "upload_label",
        "upload_kind",
        "nearest_card_text",
    ],
    "set_range": [
        "value",
        "accessible_name",
        "label_text",
        "context_text",
    ],
    "set_timecode": [
        "value",
        "accessible_name",
        "label_text",
        "context_text",
    ],
    "drag": [
        "value",
        "accessible_name",
        "label_text",
        "context_text",
    ],
}

INPUT_TYPES = {"", "text", "search", "email", "password", "number", "tel", "url"}
CLICK_INPUT_TYPES = {"button", "submit", "reset", "checkbox", "radio"}


def build_grounding_candidates(step: dict, candidates: list[dict]) -> list[dict]:
    action_type = step.get("type") or ""
    compacted = [
        compact_candidate(candidate, action_type=action_type)
        for candidate in candidates
        if _is_allowed_for_action(action_type, candidate, step)
    ]
    return sorted(
        compacted,
        key=lambda candidate: _action_rank(action_type, candidate, step),
        reverse=True,
    )


def compact_candidates(candidates: list[dict], *, action_type: str | None = None) -> list[dict]:
    return [compact_candidate(candidate, action_type=action_type) for candidate in candidates]


def compact_candidate(candidate: dict, *, action_type: str | None = None) -> dict:
    compact: dict = {}
    seen_texts: set[str] = set()
    fields = list(COMMON_FIELDS)
    if action_type:
        fields.extend(ACTION_FIELDS.get(action_type, []))
    else:
        for action_fields in ACTION_FIELDS.values():
            fields.extend(action_fields)

    for field in dict.fromkeys(fields):
        if field not in candidate:
            continue
        value = candidate.get(field)
        if value in (None, "", [], {}):
            continue
        if field in {
            "context_text",
            "nearest_card_text",
            "ancestor_text",
            "accessible_name",
            "label_text",
        }:
            if not _add_text_once(compact, field, value, seen_texts):
                continue
        else:
            compact[field] = deepcopy(value)

    if action_type == "input" and "current_value" not in compact and "value" in compact:
        compact["current_value"] = compact.pop("value")
    _ensure_single_context(compact)
    _sync_selector_metadata(compact)
    return compact


def _is_allowed_for_action(action_type: str, candidate: dict, step: dict) -> bool:
    tag = (candidate.get("tag") or "").lower()
    type_value = (candidate.get("type") or "").lower()
    role = (candidate.get("role") or "").lower()
    semantic_type = candidate.get("semantic_type")
    is_visible = candidate.get("is_visible") is not False

    if action_type in {"click", "double_click"}:
        if not is_visible:
            return False
        if semantic_type in {"file_input", "select", "textarea"}:
            return False
        if action_type == "double_click" and semantic_type == "contenteditable":
            target = " ".join(
                str(step.get(key) or "").lower() for key in ("target", "comment")
            )
            wants_canvas_text = any(
                marker in target
                for marker in (
                    "text box", "text object", "canvas text", "text editor",
                    "文字框", "文本框", "文字对象", "画布文字",
                )
            )
            return wants_canvas_text and _looks_like_canvas_text_contenteditable(candidate)
        if tag == "input" and type_value not in CLICK_INPUT_TYPES:
            return _target_wants_focus(step)
        if tag in {"button", "a"}:
            return True
        if role in {"button", "link"}:
            return True
        if semantic_type in {
            "button",
            "link",
            "upload_zone",
            "library_button",
            "result_item",
            "clickable_item",
        }:
            return True
        return False

    if action_type == "input":
        if not is_visible:
            return False
        if tag == "textarea":
            return True
        if tag == "input" and type_value in INPUT_TYPES:
            return True
        return role == "textbox" or candidate.get("contenteditable") == "true"

    if action_type == "select":
        return is_visible and tag == "select"

    if action_type == "upload":
        return semantic_type in {"file_input", "upload_zone"} or (
            tag == "input" and type_value == "file"
        )

    if action_type == "set_range":
        if not is_visible:
            return False
        if tag == "input" and type_value in {"range", "number"}:
            return True
        if tag == "input" and type_value == "text":
            return _looks_like_numeric_value_input(candidate)
        if role == "textbox" or candidate.get("contenteditable") == "true":
            return _looks_like_numeric_value_input(candidate)
        return False

    if action_type == "set_timecode":
        return is_visible and semantic_type == "composite_time_input"

    if action_type == "drag":
        if not is_visible:
            return False
        return "click" in (candidate.get("action_allowed") or []) or semantic_type == "clickable_item"

    return bool(candidate.get("selector_candidates"))


def _looks_like_canvas_text_contenteditable(candidate: dict) -> bool:
    if candidate.get("semantic_type") != "contenteditable":
        return False
    direct = " ".join(
        str(candidate.get(key) or "").lower()
        for key in ("class_name", "placeholder", "css_path", "accessible_name")
    )
    data_attrs = candidate.get("data_attrs") or {}
    if "name-input" in direct:
        return False
    return (
        "text-renderer" in direct
        or "sample text" in direct
        or "data-track-id" in data_attrs
    )


def _action_rank(action_type: str, candidate: dict, step: dict) -> tuple[int, int, int, int]:
    target = " ".join(str(step.get(key) or "") for key in ["target", "comment", "value", "path"]).lower()
    semantic_type = candidate.get("semantic_type") or ""
    tag = candidate.get("tag") or ""
    text_blob = _candidate_blob(candidate)
    score = 0

    if action_type in {"click", "double_click"}:
        requested_rank = _requested_rank(target)
        wants_ranked_item = _wants_ranked_item(target)
        wants_item_action = _wants_item_action(target)
        candidate_rank = _candidate_rank(candidate)
        has_target_evidence = _candidate_has_target_evidence(target, text_blob)
        wants_tab = _wants_tab(target)
        if wants_tab:
            if str(candidate.get("role") or "").lower() == "tab":
                score += 120
            elif semantic_type == "library_button":
                score -= 80
        if (
            requested_rank
            and candidate_rank == requested_rank
            and not wants_ranked_item
            and not has_target_evidence
        ):
            score -= 80
        if semantic_type == "clickable_item":
            score += 70
            if (
                requested_rank
                and int(candidate.get("result_rank") or 0) == requested_rank
                and (wants_ranked_item or has_target_evidence)
            ):
                score += 200
                if wants_item_action:
                    score -= 35
        elif (
            requested_rank
            and candidate_rank == requested_rank
            and (wants_ranked_item or has_target_evidence)
        ):
            score += 200
            if candidate.get("related_item_rank"):
                if _is_item_action_candidate(candidate):
                    score += 140 if wants_item_action else 20
                else:
                    score -= 40
        elif wants_ranked_item and requested_rank and semantic_type != "result_item":
            score -= 120
        if semantic_type == "library_button" and "library" not in target and "库" not in target:
            score -= 90
        if _mentions_first_result(target) and semantic_type == "result_item":
            score += 100
        if semantic_type == "result_item":
            score += 35
        if semantic_type == "library_button":
            score += 60
        elif semantic_type in {"button", "upload_zone"}:
            score += 20
        if tag == "a":
            score += 18
        upload_label = str(candidate.get("upload_label") or "").lower()
        upload_kind = str(candidate.get("upload_kind") or "").lower()
        if upload_label and upload_label in target:
            score += 30
        if upload_kind and upload_kind in target:
            score += 20
    elif action_type == "input":
        if tag in {"input", "textarea"}:
            score += 40
    elif action_type == "select":
        if tag == "select":
            score += 50
        if step.get("value") and str(step["value"]).lower() in text_blob:
            score += 20
    elif action_type == "upload":
        if semantic_type == "file_input":
            score += 60
        if candidate.get("upload_kind") and candidate["upload_kind"] in target:
            score += 30
        if candidate.get("upload_label") and candidate["upload_label"].lower() in target:
            score += 30
        if "drop files" in str(candidate.get("context_text") or "").lower():
            score += 70
    elif action_type == "set_range":
        if tag == "input" and (candidate.get("type") or "").lower() == "range":
            score += 80
        elif tag == "input" and (candidate.get("type") or "").lower() == "number":
            score += 45
        elif _looks_like_numeric_value_input(candidate):
            score += 55
    elif action_type == "set_timecode":
        if semantic_type == "composite_time_input":
            score += 80
        indexes = [
            int(item.get("index", 0))
            for item in candidate.get("selector_metadata") or []
            if item.get("selector") and int(item.get("match_count", 1)) > 1
        ]
        occurrence = min(indexes) if indexes else 0
        if any(word in target for word in ["start", "begin", "from", "开始", "起始"]):
            score += 80 if occurrence == 0 else -40
        if any(word in target for word in ["end", "until", "结束", "终止"]):
            score += 80 if occurrence > 0 else -40
    elif action_type == "drag":
        if candidate.get("is_visible"):
            score += 30
        if semantic_type == "clickable_item":
            score += 25

    if candidate.get("selector_metadata"):
        unique_count = sum(1 for item in candidate["selector_metadata"] if item.get("unique"))
        score += min(unique_count, 3) * 5
    score += _keyword_hits(target, text_blob)
    rank = int(_candidate_rank(candidate) or 9999)
    visible = 1 if candidate.get("is_visible") else 0
    return score, -rank, visible, -int(candidate.get("dom_index") or 0)


def _target_wants_focus(step: dict) -> bool:
    target = str(step.get("target") or "").lower()
    return any(word in target for word in ["focus", "聚焦", "点击输入框", "click input"])


def _looks_like_numeric_value_input(candidate: dict) -> bool:
    if (candidate.get("tag") or "").lower() != "input":
        return False
    if (candidate.get("type") or "").lower() not in {"text", "number", ""}:
        return False
    value = str(candidate.get("value") or candidate.get("current_value") or "").strip().lower()
    return bool(re.fullmatch(r"\d+(?:\.\d+)?\s*(?:x|%)?", value))


def _mentions_first_result(target: str) -> bool:
    return any(token in target for token in ["first result", "first item", "第一条", "第一个结果"])


def _requested_rank(target: str) -> int | None:
    if any(token in target for token in ["second", "2nd", "第二", "第 2", "第2", "绗簩"]):
        return 2
    if any(token in target for token in ["first", "1st", "第一", "第 1", "第1", "绗竴"]):
        return 1
    if any(token in target for token in ["third", "3rd", "第三", "第 3", "第3", "绗笁"]):
        return 3
    return None


def _wants_ranked_item(target: str) -> bool:
    if not _requested_rank(target):
        return False
    item_words = [
        "item",
        "result",
        "image",
        "photo",
        "video",
        "card",
        "tile",
        "history",
        "library",
        "条",
        "张",
        "个",
        "项",
        "图片",
        "照片",
        "视频",
        "历史",
    ]
    return any(word in target for word in item_words)


def _wants_item_action(target: str) -> bool:
    return _contains_action_word(target)


def _wants_tab(target: str) -> bool:
    return any(word in target for word in ["tab", "标签", "切换", "switch"])


def _is_item_action_candidate(candidate: dict) -> bool:
    label = " ".join(
        str(candidate.get(key) or "").lower()
        for key in ["text", "accessible_name", "aria_label", "label_text", "context_text"]
    )
    return _contains_action_word(label)


def _contains_action_word(text: str) -> bool:
    lowered = text.lower()
    tokens = set(_WORD_RE.findall(lowered))
    return bool(tokens & _ACTION_WORDS) or any(word in lowered for word in _ACTION_CJK_WORDS)


def _candidate_rank(candidate: dict) -> int | None:
    for key in ["result_rank", "related_item_rank"]:
        value = candidate.get(key)
        if value is not None:
            try:
                return int(value)
            except (TypeError, ValueError):
                pass
    data_attrs = candidate.get("data_attrs") or {}
    value = data_attrs.get("data-result-rank")
    if value is not None:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
    return None


def _candidate_blob(candidate: dict) -> str:
    parts = [
        candidate.get("text"),
        candidate.get("id"),
        candidate.get("name"),
        candidate.get("type"),
        candidate.get("role"),
        candidate.get("aria_label"),
        candidate.get("placeholder"),
        candidate.get("accessible_name"),
        candidate.get("label_text"),
        candidate.get("context_text"),
        candidate.get("upload_label"),
        candidate.get("upload_kind"),
        candidate.get("related_item_rank"),
        candidate.get("related_item_context"),
        candidate.get("href"),
    ]
    options = candidate.get("options") or []
    parts.extend(str(option.get("text") or option.get("value") or "") for option in options if isinstance(option, dict))
    return " ".join(str(part).lower() for part in parts if part)


def _candidate_has_target_evidence(target: str, blob: str) -> bool:
    if not _satisfies_strict_font_qualifiers(target, blob):
        return False
    return _keyword_hits(target, blob) > 0


def _satisfies_strict_font_qualifiers(target: str, blob: str) -> bool:
    target_lower = str(target or "").lower()
    if not any(word in target_lower for word in ["font", "sans", "serif", "simhei", "yahei", "pingfang", "noto", "source han", "cjk"]):
        return True
    blob_lower = str(blob or "").lower()
    strict_terms = [
        "cjk",
        " sc",
        " tc",
        "simhei",
        "yahei",
        "pingfang",
        "source han",
        "microsoft yahei",
        "chinese",
        "simplified chinese",
        "traditional chinese",
    ]
    required = [term for term in strict_terms if term in f" {target_lower} "]
    if not required:
        return True
    return all(term.strip() in blob_lower for term in required)


def _keyword_hits(text: str, blob: str) -> int:
    score = 0
    for token in text.replace("_", " ").replace("-", " ").split():
        if len(token) > 1 and token in blob:
            score += 3
    return score


def _add_text_once(compact: dict, field: str, value: object, seen_texts: set[str]) -> bool:
    if not isinstance(value, str):
        compact[field] = deepcopy(value)
        return True
    normalized = " ".join(value.split())
    if not normalized or normalized in seen_texts:
        return False
    seen_texts.add(normalized)
    compact[field] = normalized
    return True


def _ensure_single_context(compact: dict) -> None:
    nearest = compact.get("nearest_card_text")
    context = compact.get("context_text")
    if nearest and not context:
        compact["context_text"] = nearest
    elif nearest and context == nearest:
        compact.pop("nearest_card_text", None)


def _sync_selector_metadata(compact: dict) -> None:
    selectors = compact.get("selector_candidates") or []
    metadata = compact.get("selector_metadata") or []
    if not selectors or metadata:
        return
    compact["selector_metadata"] = [
        {
            "selector": selector,
            "index": 0,
            "match_count": 1,
            "unique": True,
        }
        for selector in selectors
    ]

from __future__ import annotations

import re


_TOKEN_RE = re.compile(r"[A-Za-z0-9_\-]+|[\u4e00-\u9fff]+")
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


def _step_target_text(step: dict) -> str:
    # The target is the grounding contract. Comments describe intent and often
    # contain unrelated nouns (for example "uploaded video") that must not
    # overpower the visible label requested by the target.
    return str(step.get("target") or step.get("comment") or "")


class AmbiguousTargetError(RuntimeError):
    """Raised when DOM candidates are too close to choose safely."""

    reason = "ambiguous_target"

    def __init__(self, target: object, candidates: list[dict], scores: list[int]) -> None:
        self.target = target
        self.candidates = candidates
        self.scores = scores
        candidate_ids = [
            str(candidate.get("candidate_id") or candidate.get("dom_index") or "?")
            for candidate in candidates
        ]
        super().__init__(
            f"Ambiguous DOM target {target!r}; top candidates are too close: "
            f"{', '.join(candidate_ids)}"
        )


class SelectorGrounder:
    def ground(self, *, step: dict, candidates: list[dict], context: dict) -> dict:
        step_type = step.get("type")
        if step_type == "goto":
            return {
                "target": step.get("target"),
                "candidate_id": None,
                "selectors": [],
                "reason": "goto step does not need DOM grounding",
            }

        ranked = sorted(
            (
                (self._score_candidate(step=step, candidate=candidate, context=context), candidate)
                for candidate in candidates
                if candidate.get("selector_candidates")
                and _satisfies_explicit_target_constraints(step, candidate)
                and _has_required_target_evidence(step, candidate)
            ),
            key=lambda item: item[0],
            reverse=True,
        )
        if not ranked or ranked[0][0] <= 0:
            raise RuntimeError(f"No DOM candidate matched target: {step.get('target')}")

        ambiguous = _ambiguous_top_candidates(step, ranked)
        if ambiguous:
            raise AmbiguousTargetError(
                step.get("target"),
                [candidate for _, candidate in ambiguous],
                [score for score, _ in ambiguous],
            )

        score, candidate = ranked[0]
        return {
            "target": step.get("target"),
            "candidate_id": candidate["candidate_id"],
            "selectors": candidate["selector_candidates"],
            "selector_metadata": candidate.get("selector_metadata", []),
            "reason": f"selected candidate {candidate['candidate_id']} with score {score}",
            "candidate": candidate,
        }

    def _score_candidate(self, *, step: dict, candidate: dict, context: dict) -> int:
        step_type = step.get("type")
        target = _step_target_text(step)
        lowered_target = target.lower()
        intent_lower = f"{target} {step.get('comment') or ''}".lower()
        requested_rank = _requested_rank(lowered_target)
        candidate_rank = _candidate_rank(candidate)
        value = str(step.get("value") or step.get("path") or "")
        text_blob = _candidate_text_blob(candidate)
        score = 0
        allowed_actions = candidate.get("action_allowed") or []
        equivalent_allowed = (
            (step_type == "set_range" and "input" in allowed_actions)
            or (step_type == "drag" and "click" in allowed_actions)
            or (step_type == "double_click" and "click" in allowed_actions)
        )
        if allowed_actions and step_type not in allowed_actions and not equivalent_allowed:
            if not (step_type == "click" and "click_upload_zone" in allowed_actions):
                score -= 60

        if step_type == "input":
            if candidate.get("tag") in {"input", "textarea"}:
                score += 30
            if candidate.get("type") in {"text", "search", "password", None}:
                score += 8
            if candidate.get("type") in {"file", "checkbox", "radio", "hidden", "submit"}:
                score -= 20
        elif step_type == "select":
            if candidate.get("tag") == "select":
                score += 35
        elif step_type == "upload":
            if candidate.get("semantic_type") == "file_input" or (
                candidate.get("tag") == "input" and candidate.get("type") == "file"
            ):
                score += 40
            if "upload" in allowed_actions:
                score += 25
            rect = candidate.get("rect") or {}
            hit_area = float(rect.get("width") or 0) * float(rect.get("height") or 0)
            if hit_area > 0:
                score += min(35, 10 + int(hit_area / 5000))
            else:
                score -= 10
            if "drop files" in str(candidate.get("context_text") or "").lower():
                score += 70
        elif step_type == "set_range":
            if candidate.get("tag") == "input" and candidate.get("type") == "range":
                score += 80
            elif candidate.get("tag") == "input" and candidate.get("type") == "number":
                score += 45
            elif _looks_like_numeric_value_input(candidate):
                score += 55
        elif step_type == "set_timecode":
            if candidate.get("semantic_type") == "composite_time_input":
                score += 90
            indexes = [
                int(item.get("index", 0))
                for item in candidate.get("selector_metadata") or []
                if item.get("selector") and int(item.get("match_count", 1)) > 1
            ]
            occurrence = min(indexes) if indexes else 0
            if any(word in lowered_target for word in ["start", "begin", "from", "开始", "起始"]):
                score += 100 if occurrence == 0 else -60
            if any(word in lowered_target for word in ["end", "until", "结束", "终止"]):
                score += 100 if occurrence > 0 else -60
        elif step_type == "drag":
            if candidate.get("is_visible"):
                score += 35
            if candidate.get("semantic_type") == "clickable_item":
                score += 25
        elif step_type in {"click", "double_click"}:
            wants_ranked_item = _wants_ranked_item(lowered_target)
            wants_item_action = _wants_item_action(intent_lower)
            wants_tab = _wants_tab(intent_lower)
            if wants_tab:
                if str(candidate.get("role") or "").lower() == "tab":
                    score += 140
                elif candidate.get("semantic_type") == "library_button":
                    score -= 90
            if _wants_text_color_control(lowered_target):
                if _looks_like_text_color_control(candidate):
                    score += 240
                elif _is_text_tool_navigation(candidate):
                    score -= 260
            if candidate.get("semantic_type") == "clickable_item":
                score += 45
                if requested_rank and int(candidate.get("result_rank") or 0) == requested_rank:
                    score += 200
                    if wants_item_action:
                        score -= 35
            elif requested_rank and candidate_rank == requested_rank:
                score += 200
                if candidate.get("related_item_rank"):
                    if _is_item_action_candidate(candidate):
                        score += 160 if wants_item_action else 15
                    else:
                        score -= 40
            if (
                step_type == "double_click"
                and candidate.get("semantic_type") == "contenteditable"
                and _target_wants_canvas_text_editor(lowered_target)
                and _looks_like_canvas_text_contenteditable(candidate)
            ):
                score += 180
            elif wants_ranked_item and requested_rank and candidate.get("semantic_type") != "result_item":
                score -= 120
            if (
                candidate.get("semantic_type") == "library_button"
                and "library" not in lowered_target
                and "库" not in lowered_target
            ):
                score -= 80
            if candidate.get("tag") in {"button", "a", "input"}:
                score += 24
            if candidate.get("tag") == "a" and _label_is_strong_target_match(target, candidate):
                score += 240
            if candidate.get("semantic_type") == "library_button":
                score += 18
                if (
                    ("add image" in lowered_target or "添加图片" in lowered_target)
                    and (
                        candidate.get("upload_kind") == "image"
                        or "image" in str(candidate.get("upload_label") or "").lower()
                    )
                ):
                    score += 120
            if candidate.get("type") in {"submit", "button"}:
                score += 10
            if candidate.get("tag") == "input" and candidate.get("type") not in {
                "submit",
                "button",
                "checkbox",
                "radio",
            }:
                score -= 30
            score += _exact_label_score(target, candidate)
            if step_type == "double_click":
                requested_file = _file_name_in_target(target)
                own_label = " ".join(
                    str(candidate.get(key) or "").strip().lower()
                    for key in ["text", "accessible_name", "aria_label"]
                )
                if requested_file and requested_file in own_label:
                    score += 220
                    if any(
                        "thumb-item" in str(selector)
                        for selector in candidate.get("selector_candidates") or []
                    ):
                        # Dispatch the double-click on the interactive card,
                        # not on a filename child that may only select text.
                        score += 160

        score += _keyword_score(target, text_blob) * 6
        if value:
            score += _keyword_score(value, text_blob) * 2

        if "第一" in target or "first" in lowered_target:
            if candidate.get("data_attrs", {}).get("data-result-rank") == "1":
                score += 35
            if "alpha" in text_blob.lower():
                score += 25
            if candidate.get("tag") == "a":
                score += 25
        if requested_rank and candidate_rank == requested_rank:
            if candidate.get("related_item_rank"):
                if _is_item_action_candidate(candidate) and _wants_item_action(lowered_target):
                    score += 80
                elif not _is_item_action_candidate(candidate):
                    score -= 40
            elif candidate.get("tag") in {"a", "button"}:
                score += 80
            elif candidate.get("semantic_type") != "clickable_item":
                score -= 80

        if "submit" in lowered_target or "提交" in target:
            if (candidate.get("text") or "").strip().lower() == "submit":
                score += 25
            if candidate.get("type") == "submit":
                score += 20

        if ("text input" in lowered_target or "文本框" in target) and candidate.get("name") == "my-text":
            score += 40
        if ("dropdown" in lowered_target or "下拉" in target) and candidate.get("name") == "my-select":
            score += 40
        if ("search" in lowered_target or "搜索" in target) and candidate.get("name") == "q":
            score += 30
        if ("name" in lowered_target or "姓名" in target) and candidate.get("name") == "name":
            score += 30
        if ("type" in lowered_target or "类型" in target) and candidate.get("name") == "type":
            score += 30
        if ("upload" in lowered_target or "文件" in target) and candidate.get("type") == "file":
            score += 30

        if ("motion" in lowered_target or "video" in lowered_target) and (
            candidate.get("upload_kind") == "video"
            or "motion" in (candidate.get("upload_label") or "").lower()
        ):
            score += 45
        if ("image" in lowered_target or "photo" in lowered_target) and (
            candidate.get("upload_kind") == "image"
            or "image" in (candidate.get("upload_label") or "").lower()
        ):
            score += 45

        if candidate.get("is_visible") is False and step_type != "upload":
            score -= 25

        return score


def _candidate_text_blob(candidate: dict) -> str:
    parts = [
        candidate.get("tag"),
        candidate.get("text"),
        candidate.get("id"),
        candidate.get("name"),
        candidate.get("type"),
        candidate.get("role"),
        candidate.get("aria_label"),
        candidate.get("placeholder"),
        candidate.get("value"),
        candidate.get("accessible_name"),
        candidate.get("label_text"),
        candidate.get("primary_context_text"),
        candidate.get("context_text"),
        candidate.get("upload_label"),
        candidate.get("upload_kind"),
        candidate.get("nearest_card_text"),
        candidate.get("ancestor_text"),
        candidate.get("related_item_rank"),
        candidate.get("related_item_context"),
        candidate.get("css_path"),
    ]
    data_attrs = candidate.get("data_attrs") or {}
    parts.extend(data_attrs.keys())
    parts.extend(data_attrs.values())
    selectors = candidate.get("selector_candidates") or []
    parts.extend(selectors)
    for item in candidate.get("context_chain") or []:
        if isinstance(item, dict):
            parts.extend(
                [
                    item.get("tag"),
                    item.get("role"),
                    item.get("aria_label"),
                    item.get("text"),
                ]
            )
    return " ".join(str(part) for part in parts if part)


def _keyword_score(text: str, blob: str) -> int:
    blob_lower = blob.lower()
    score = 0
    for token in _TOKEN_RE.findall(text):
        normalized = token.lower()
        if len(normalized) <= 1:
            continue
        if normalized in blob_lower:
            score += 1
    return score


def _file_name_in_target(target: str) -> str | None:
    match = re.search(
        r"(?i)([\w.\-\u4e00-\u9fff]+\.(?:mp4|mov|webm|avi|mkv|mp3|wav|jpg|jpeg|png|gif|pdf|txt))",
        str(target or ""),
    )
    return match.group(1).lower() if match else None


def _exact_label_score(target: str, candidate: dict) -> int:
    target_lower = " ".join(target.lower().split())
    if not target_lower:
        return 0
    target_tokens = _meaningful_target_tokens(target_lower)
    labels = [
        candidate.get("text"),
        candidate.get("accessible_name"),
        candidate.get("aria_label"),
        candidate.get("label_text"),
    ]
    score = 0
    for label in labels:
        label_lower = " ".join(str(label or "").lower().split())
        if not label_lower:
            continue
        if label_lower == target_lower:
            score += 80
        elif target_lower in label_lower:
            score += 35
        elif len(label_lower) >= 3 and label_lower in target_lower:
            label_hits = _meaningful_keyword_hits(label_lower, target_lower)
            if len(target_tokens) <= 1 or label_hits >= 2:
                score += 35
    return score


def _label_is_strong_target_match(target: str, candidate: dict) -> bool:
    target_lower = " ".join(target.lower().split())
    if not target_lower:
        return False
    for key in ["text", "accessible_name", "aria_label", "label_text"]:
        label = " ".join(str(candidate.get(key) or "").lower().split())
        if label and len(label) >= 3 and label in target_lower:
            return True
    return False


def _requested_rank(target_lower: str) -> int | None:
    if any(token in target_lower for token in ["second", "2nd", "第二", "第 2", "第2", "绗簩"]):
        return 2
    if any(token in target_lower for token in ["first", "1st", "第一", "第 1", "第1", "绗竴"]):
        return 1
    if any(token in target_lower for token in ["third", "3rd", "第三", "第 3", "第3", "绗笁"]):
        return 3
    return None


def _wants_ranked_item(target_lower: str) -> bool:
    if not _requested_rank(target_lower):
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
    return any(word in target_lower for word in item_words)


def _wants_item_action(target_lower: str) -> bool:
    return _contains_action_word(target_lower)


def _wants_tab(target_lower: str) -> bool:
    return any(word in target_lower for word in ["tab", "标签", "切换", "switch"])


def _is_item_action_candidate(candidate: dict) -> bool:
    label = " ".join(
        str(candidate.get(key) or "").lower()
        for key in ["text", "accessible_name", "aria_label", "label_text", "context_text"]
    )
    return _contains_action_word(label)


def _satisfies_explicit_target_constraints(step: dict, candidate: dict) -> bool:
    target = _step_target_text(step).lower()
    if _wants_text_color_control(target):
        return _looks_like_text_color_control(candidate)
    if step.get("type") in {"drag", "set_range"} and any(
        word in target for word in ["range", "slider", "滑块", "范围"]
    ):
        if candidate.get("tag") == "input" and candidate.get("type") in {
            "range",
            "number",
        }:
            return True
        return step.get("type") == "set_range" and _looks_like_numeric_value_input(candidate)
    if step.get("type") not in {"click", "double_click"}:
        return True

    wants_canvas_object = any(
        phrase in target
        for phrase in [
            "in preview canvas", "on preview canvas", "in canvas", "on canvas",
            "预览画布中", "画布中的", "画布上", "画布内",
        ]
    )
    if wants_canvas_object:
        if (
            step.get("type") == "double_click"
            and candidate.get("semantic_type") == "contenteditable"
            and _target_wants_canvas_text_editor(target)
            and _looks_like_canvas_text_contenteditable(candidate)
        ):
            return True
        # Canvas scene objects have no reliable DOM node. A media-library
        # thumbnail or the Canvas sidebar tab is not an acceptable substitute.
        return str(candidate.get("tag") or "").lower() == "canvas"

    wants_library_control = "library" in target or "库" in target
    wants_image_area = "add image" in target or "添加图片" in target
    wants_motion_area = (
        "add motion" in target
        or "motion" in target
        or "video" in target
        or "添加动作" in target
        or "添加视频" in target
    )
    if wants_library_control and (wants_image_area or wants_motion_area):
        if candidate.get("semantic_type") != "library_button":
            return False
        upload_kind = str(candidate.get("upload_kind") or "").lower()
        upload_label = str(candidate.get("upload_label") or "").lower()
        if wants_image_area:
            return upload_kind == "image" or "image" in upload_label
        return upload_kind == "video" or "motion" in upload_label

    if not _wants_tab(target):
        return True

    label = " ".join(
        str(candidate.get(key) or "")
        for key in ["text", "accessible_name", "aria_label", "label_text"]
    ).lower()
    target_name = str(step.get("target") or "").lower()
    ignored = {
        "tab",
        "button",
        "click",
        "open",
        "select",
        "switch",
        "to",
        "the",
    }
    required_tokens = [
        token
        for token in _WORD_RE.findall(target_name)
        if len(token) > 1 and token not in ignored
    ]
    if required_tokens:
        return all(token in label for token in required_tokens)

    cjk_target = re.sub(r"(标签页|标签|按钮|点击|切换|打开|选择)", "", target_name).strip()
    return not cjk_target or cjk_target in label


def _contains_action_word(text: str) -> bool:
    lowered = text.lower()
    tokens = set(_WORD_RE.findall(lowered))
    return bool(tokens & _ACTION_WORDS) or any(word in lowered for word in _ACTION_CJK_WORDS)


def _ambiguous_top_candidates(
    step: dict, ranked: list[tuple[int, dict]]
) -> list[tuple[int, dict]]:
    if len(ranked) < 2:
        return []
    target = _step_target_text(step)
    target_lower = target.lower()
    if _requested_rank(target_lower):
        return []

    top_score, top_candidate = ranked[0]
    second_score, second_candidate = ranked[1]
    if top_score <= 0 or second_score <= 0:
        return []
    if top_score - second_score > 5:
        return []
    if _same_dom_identity(top_candidate, second_candidate):
        return []
    if _same_visual_control(top_candidate, second_candidate):
        return []

    top_exact = _exact_label_score(target, top_candidate)
    second_exact = _exact_label_score(target, second_candidate)
    if abs(top_exact - second_exact) >= 35:
        return []

    top_context = _disambiguating_context_score(target, top_candidate)
    second_context = _disambiguating_context_score(target, second_candidate)
    if abs(top_context - second_context) >= 2:
        return []

    return [ranked[0], ranked[1]]


def _same_visual_control(left: dict, right: dict) -> bool:
    if left.get("semantic_type") != "clickable_item" or right.get("semantic_type") != "clickable_item":
        return False
    left_label = _semantic_label(left)
    right_label = _semantic_label(right)
    if not left_label or left_label != right_label:
        return False
    return _rect_contains_center(left.get("rect") or {}, right.get("rect") or {}) or _rect_contains_center(
        right.get("rect") or {}, left.get("rect") or {}
    )


def _semantic_label(candidate: dict) -> str:
    labels = [
        " ".join(str(candidate.get(key) or "").lower().split())
        for key in ("text", "accessible_name", "aria_label")
        if candidate.get(key)
    ]
    return labels[0] if labels and all(label == labels[0] for label in labels) else ""


def _rect_contains_center(container: dict, inner: dict) -> bool:
    width = float(container.get("width") or 0)
    height = float(container.get("height") or 0)
    inner_width = float(inner.get("width") or 0)
    inner_height = float(inner.get("height") or 0)
    if min(width, height, inner_width, inner_height) <= 0:
        return False
    x = float(container.get("x") or 0)
    y = float(container.get("y") or 0)
    center_x = float(inner.get("x") or 0) + inner_width / 2
    center_y = float(inner.get("y") or 0) + inner_height / 2
    return x <= center_x <= x + width and y <= center_y <= y + height


def _has_required_target_evidence(step: dict, candidate: dict) -> bool:
    step_type = step.get("type")
    if step_type == "set_timecode":
        return candidate.get("semantic_type") == "composite_time_input"
    if step_type not in {"click", "double_click", "input", "drag", "set_range"}:
        return True
    target = _step_target_text(step)
    target_lower = target.lower()
    if _wants_text_color_control(target_lower):
        return _looks_like_text_color_control(candidate)
    if (
        step_type == "double_click"
        and candidate.get("semantic_type") == "contenteditable"
        and _target_wants_canvas_text_editor(target_lower)
        and _looks_like_canvas_text_contenteditable(candidate)
    ):
        return True
    if not _meaningful_target_tokens(target_lower):
        return True
    if not _satisfies_strict_font_qualifiers(target_lower, _candidate_direct_blob(candidate)):
        return False
    # Rank is standalone evidence only for an explicitly ranked item/card/result.
    # A phrase such as "first Add text button" must still match the core label;
    # otherwise an unrelated global result_rank=1 element can win.
    if (
        _requested_rank(target_lower)
        and _candidate_rank(candidate)
        and _wants_ranked_item(target_lower)
    ):
        return True
    if _has_strong_label_evidence(target, candidate):
        return True
    if step_type in {"input", "set_range"}:
        input_evidence = " ".join(
            [
                _candidate_direct_blob(candidate),
                str(candidate.get("context_text") or ""),
                str(candidate.get("nearest_card_text") or ""),
                str(candidate.get("ancestor_text") or ""),
            ]
        )
        if _meaningful_keyword_hits(target_lower, input_evidence) > 0:
            return True
    keyword_hits = _meaningful_keyword_hits(target_lower, _candidate_direct_blob(candidate))
    meaningful_tokens = _meaningful_target_tokens(target_lower)
    if len(meaningful_tokens) >= 2:
        if keyword_hits >= 2:
            return True
    elif keyword_hits > 0:
        return True
    semantic_type = str(candidate.get("semantic_type") or "").lower()
    if semantic_type in {
        "library_button",
        "result_item",
        "clickable_item",
        "upload_zone",
        "file_input",
    } and _disambiguating_context_score(target, candidate) > 0:
        return True
    if "library" in target_lower and semantic_type == "library_button":
        return True
    if any(word in target_lower for word in ["upload", "file", "video", "image", "文件", "视频", "图片"]):
        return semantic_type in {"upload_zone", "file_input", "library_button"}
    return False


def _target_wants_canvas_text_editor(target: str) -> bool:
    lowered = str(target or "").lower()
    return any(
        marker in lowered
        for marker in (
            "text box", "text object", "canvas text", "text editor",
            "文字框", "文本框", "文字对象", "画布文字",
        )
    )


def _wants_text_color_control(target: str) -> bool:
    lowered = str(target or "").lower()
    has_color = any(word in lowered for word in ("color", "colour", "颜色", "色彩"))
    has_text = any(word in lowered for word in ("text", "font", "文字", "文本", "字体"))
    return has_color and has_text


def _looks_like_text_color_control(candidate: dict) -> bool:
    if candidate.get("is_visible") is False:
        return False
    direct = _candidate_direct_blob(candidate)
    context = " ".join(
        str(candidate.get(key) or "").lower()
        for key in ("context_text", "primary_context_text", "nearest_card_text", "class_name")
    )
    is_color_control = (
        "color picker" in direct
        or "color-picker" in direct
        or str(candidate.get("type") or "").lower() == "color"
    )
    is_font_property_area = any(
        marker in context for marker in ("font", "open sans", "font size", "block-content-font")
    )
    return is_color_control and is_font_property_area


def _is_text_tool_navigation(candidate: dict) -> bool:
    label = _semantic_label(candidate)
    classes = str(candidate.get("class_name") or "").lower()
    return label == "text" or ("side-menu" in classes and "text" in classes)


def _looks_like_canvas_text_contenteditable(candidate: dict) -> bool:
    if candidate.get("semantic_type") != "contenteditable":
        return False
    direct = " ".join(
        str(candidate.get(key) or "").lower()
        for key in ("class_name", "placeholder", "css_path", "accessible_name")
    )
    if "name-input" in direct:
        return False
    return (
        "text-renderer" in direct
        or "sample text" in direct
        or "data-track-id" in (candidate.get("data_attrs") or {})
    )


def _meaningful_target_tokens(target_lower: str) -> list[str]:
    ignored = {
        "a",
        "an",
        "and",
        "the",
        "to",
        "of",
        "button",
        "btn",
        "click",
        "press",
        "open",
        "select",
        "choose",
        "apply",
        "confirm",
        "tool",
        "settings",
        "setting",
        "panel",
        "link",
    }
    raw_tokens = [
        token
        for token in _WORD_RE.findall(target_lower)
        if len(token) > 1 and token not in ignored
    ]
    tokens = list(dict.fromkeys(raw_tokens))
    cjk = re.sub(r"(按钮|点击|打开|选择|确认|应用|工具|设置|面板)", "", target_lower).strip()
    if cjk and re.search(r"[\u4e00-\u9fff]", cjk):
        tokens.append(cjk)
    return tokens


def _has_strong_label_evidence(target: str, candidate: dict) -> bool:
    target_lower = " ".join(target.lower().split())
    target_tokens = _meaningful_target_tokens(target_lower)
    if not target_lower:
        return False
    for key in ["text", "accessible_name", "aria_label", "label_text"]:
        label_lower = " ".join(str(candidate.get(key) or "").lower().split())
        if not label_lower:
            continue
        if label_lower == target_lower:
            return True
        if len(label_lower) >= 3 and target_lower in label_lower:
            return True
        if len(label_lower) >= 3 and label_lower in target_lower:
            if len(label_lower.split()) >= 2:
                return True
            label_hits = _meaningful_keyword_hits(label_lower, target_lower)
            if len(target_tokens) <= 1 or label_hits >= 2:
                return True
    return False


def _candidate_direct_blob(candidate: dict) -> str:
    parts = [
        candidate.get("text"),
        candidate.get("id"),
        candidate.get("name"),
        candidate.get("role"),
        candidate.get("aria_label"),
        candidate.get("accessible_name"),
        candidate.get("label_text"),
        candidate.get("placeholder"),
        candidate.get("value"),
    ]
    data_attrs = candidate.get("data_attrs") or {}
    parts.extend(data_attrs.values())
    return " ".join(str(part) for part in parts if part)


def _satisfies_strict_font_qualifiers(target_lower: str, blob: str) -> bool:
    if not any(word in target_lower for word in ["font", "sans", "serif", "simhei", "yahei", "pingfang", "noto", "source han", "cjk"]):
        return True
    blob_lower = blob.lower()
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


def _looks_like_numeric_value_input(candidate: dict) -> bool:
    if str(candidate.get("tag") or "").lower() != "input":
        return False
    if str(candidate.get("type") or "").lower() not in {"text", "number", ""}:
        return False
    value = str(candidate.get("value") or candidate.get("current_value") or "").strip().lower()
    return bool(re.fullmatch(r"\d+(?:\.\d+)?\s*(?:x|%)?", value))


def _meaningful_keyword_hits(target_lower: str, blob: str) -> int:
    blob_lower = blob.lower()
    hits = 0
    for token in _meaningful_target_tokens(target_lower):
        if token in blob_lower:
            hits += 1
    bilingual_aliases = [
        (("速度",), ("speed",)),
        (("不透明度",), ("opacity",)),
        (("文字", "文本", "标题"), ("text", "title")),
        (("裁剪", "修剪"), ("trim",)),
        (("开始", "起始"), ("start",)),
        (("结束", "终止"), ("end",)),
    ]
    for cjk_terms, english_terms in bilingual_aliases:
        crosses_language = (
            any(term in target_lower for term in cjk_terms)
            and any(term in blob_lower for term in english_terms)
        ) or (
            any(term in target_lower for term in english_terms)
            and any(term in blob_lower for term in cjk_terms)
        )
        if crosses_language:
            hits += 1
    return hits


def _same_dom_identity(first: dict, second: dict) -> bool:
    first_id = first.get("candidate_id")
    second_id = second.get("candidate_id")
    if first_id and second_id and first_id == second_id:
        return True
    first_dom_index = first.get("dom_index")
    second_dom_index = second.get("dom_index")
    return first_dom_index is not None and first_dom_index == second_dom_index


def _disambiguating_context_score(target: str, candidate: dict) -> int:
    target_lower = target.lower()
    score = 0
    for key in ["context_text", "ancestor_text", "nearest_card_text", "upload_label", "upload_kind"]:
        value = str(candidate.get(key) or "").lower()
        if value and value in target_lower:
            score += 1
    return score


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

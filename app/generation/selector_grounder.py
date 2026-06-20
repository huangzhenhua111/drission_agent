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
            ),
            key=lambda item: item[0],
            reverse=True,
        )
        if not ranked or ranked[0][0] <= 0:
            raise RuntimeError(f"No DOM candidate matched target: {step.get('target')}")

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
        target = " ".join(str(step.get(key) or "") for key in ["target", "comment"])
        lowered_target = target.lower()
        requested_rank = _requested_rank(lowered_target)
        candidate_rank = _candidate_rank(candidate)
        value = str(step.get("value") or step.get("path") or "")
        text_blob = _candidate_text_blob(candidate)
        score = 0
        allowed_actions = candidate.get("action_allowed") or []
        if allowed_actions and step_type not in allowed_actions:
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
        elif step_type == "click":
            wants_ranked_item = _wants_ranked_item(lowered_target)
            wants_item_action = _wants_item_action(lowered_target)
            wants_tab = _wants_tab(lowered_target)
            if wants_tab:
                if str(candidate.get("role") or "").lower() == "tab":
                    score += 140
                elif candidate.get("semantic_type") == "library_button":
                    score -= 90
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


def _exact_label_score(target: str, candidate: dict) -> int:
    target_lower = " ".join(target.lower().split())
    if not target_lower:
        return 0
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
        elif label_lower in target_lower or target_lower in label_lower:
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
    target = " ".join(str(step.get(key) or "") for key in ["target", "comment"]).lower()
    if step.get("type") != "click":
        return True

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

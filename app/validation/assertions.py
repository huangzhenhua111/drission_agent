from __future__ import annotations

import re


_WORD_RE = re.compile(r"[a-z0-9_]+")
_ACTION_WORDS = {"select", "choose", "pick", "add", "insert", "use", "reuse", "apply"}
_ACTION_CJK_WORDS = ["选", "选择", "添加", "加入", "使用", "复用", "采用"]


def validate_capture_success(
    *,
    task: str,
    captured_actions: list[dict],
    success_assertions: list[dict],
) -> list[str]:
    issues: list[str] = []
    if not captured_actions:
        return ["no captured actions"]

    final = captured_actions[-1]
    final_url = str(final.get("after_url") or "")
    final_title = str(final.get("after_title") or "")
    final_text = str(final.get("after_text_excerpt") or "")
    if _is_auth_url_or_title(final_url, final_title, final_text):
        issues.append(f"ended on authentication page: {final_url}")

    for assertion in success_assertions:
        if not isinstance(assertion, dict):
            issues.append(f"invalid assertion value: {assertion!r}")
            continue
        assertion_type = assertion.get("type")
        value = str(assertion.get("value") or "")
        if assertion_type == "url_contains" and value not in final_url:
            issues.append(f"url assertion failed: expected {value!r} in {final_url!r}")
        elif assertion_type == "title_contains" and value not in final_title:
            issues.append(f"title assertion failed: expected {value!r} in {final_title!r}")
        elif assertion_type not in {"url_contains", "title_contains"}:
            issues.append(f"unsupported assertion type: {assertion_type!r}")

    task_lower = task.lower()
    if ("history" in task_lower or "历史" in task_lower) and (
        "image" in task_lower or "图片" in task_lower or "图" in task_lower
    ):
        last_target = str(final.get("target") or "").lower()
        if "second" not in last_target and "第二" not in last_target and "第2" not in last_target:
            issues.append("history image task did not end with the requested history item target")
    if _wanted_item_selection(task, final) and _clicked_preview_only(final):
        issues.append("selection/add task clicked a preview-only item instead of a select/use/add action")
    if _task_wants_my_library(task) and not _actions_include_my_library(captured_actions):
        issues.append("task requested My Library but captured workflow did not select or verify My Library")
    for action in captured_actions:
        postcondition = action.get("postcondition")
        requires_visual_text = bool(
            isinstance(postcondition, dict)
            and postcondition.get("requires_visual_text_rendering")
        )
        requires_visual_text = requires_visual_text or (
            action.get("semantic_type") == "contenteditable"
            and _contains_cjk(str(action.get("value") or ""))
        )
        if requires_visual_text and not action.get("rendered_text_readable"):
            issues.append(
                "contenteditable CJK text matched DOM but readable visual rendering was not verified"
            )
    return issues


def _is_auth_url_or_title(url: str, title: str, text: str = "") -> bool:
    lowered_url = url.lower()
    lowered_title = title.lower()
    lowered_text = text.lower()
    markers = [
        "/login",
        "appleid.apple.com/auth",
        "accounts.google.com",
        "oauth",
        "signin",
        "sign-in",
    ]
    signed_out_markers = [
        "sign in with email",
        "sign in with google",
        "continue with google",
        "continue with apple",
        "continue with email",
        "forgot password",
        "登录",
    ]
    return (
        any(marker in lowered_url for marker in markers)
        or "login" in lowered_title
        or "sign in" in lowered_title
        or any(marker in lowered_text for marker in signed_out_markers)
    )


def _contains_cjk(text: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", text))


def _wanted_item_selection(task: str, action: dict) -> bool:
    text = " ".join(
        str(value or "")
        for value in [
            task,
            action.get("target"),
            action.get("comment"),
        ]
    ).lower()
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
    tokens = set(_WORD_RE.findall(text))
    has_action = bool(tokens & _ACTION_WORDS) or any(word in text for word in _ACTION_CJK_WORDS)
    return any(word in text for word in item_words) and has_action


def _clicked_preview_only(action: dict) -> bool:
    text = " ".join(
        str(value or "")
        for value in [
            action.get("chosen_selector"),
            action.get("context_text"),
            action.get("grounding_reason"),
            action.get("semantic_type"),
        ]
    ).lower()
    preview_markers = ["open generation preview", "open preview", "preview", "预览"]
    tokens = set(_WORD_RE.findall(text))
    has_action = bool(tokens & _ACTION_WORDS) or any(word in text for word in _ACTION_CJK_WORDS)
    if not any(marker in text for marker in preview_markers):
        return False
    if action.get("semantic_type") == "clickable_item":
        return True
    return not has_action


def _task_wants_my_library(task: str) -> bool:
    text = task.lower()
    return (
        "my library" in text
        or "my history library" in text
        or "user library" in text
        or "personal library" in text
        or "我的库" in text
        or "我的历史库" in text
        or ("我的" in text and "库" in text)
    )


def _actions_include_my_library(actions: list[dict]) -> bool:
    for action in actions:
        requested_text = " ".join(
            str(value or "")
            for value in [
                action.get("target"),
                action.get("comment"),
            ]
        ).lower()
        if not (
            "my library" in requested_text
            or "我的库" in requested_text
            or "我的历史库" in requested_text
        ):
            continue
        actual_text = " ".join(
            str(value or "")
            for value in [
                action.get("candidate_text"),
                action.get("candidate_accessible_name"),
                action.get("candidate_aria_label"),
                action.get("chosen_selector"),
                action.get("context_text"),
            ]
        ).lower()
        if "my library" in actual_text:
            return True
    return False

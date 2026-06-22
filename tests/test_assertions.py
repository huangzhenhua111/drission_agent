from __future__ import annotations

from app.validation.assertions import validate_capture_success


def test_validate_capture_success_rejects_auth_final_page() -> None:
    issues = validate_capture_success(
        task="open app",
        captured_actions=[
            {
                "type": "click",
                "target": "Library",
                "after_url": "https://viggle.ai/login",
                "after_title": "Viggle AI",
            }
        ],
        success_assertions=[],
    )

    assert issues == ["ended on authentication page: https://viggle.ai/login"]


def test_validate_capture_success_checks_success_assertions() -> None:
    issues = validate_capture_success(
        task="submit form",
        captured_actions=[
            {
                "type": "click",
                "target": "Submit",
                "after_url": "https://example.com/submitted",
                "after_title": "Done",
            }
        ],
        success_assertions=[{"type": "url_contains", "value": "submitted"}],
    )

    assert issues == []


def test_validate_capture_success_rejects_preview_only_item_selection() -> None:
    issues = validate_capture_success(
        task="打开 app，在历史库里面选第二张图片",
        captured_actions=[
            {
                "type": "click",
                "target": "second image in history library",
                "comment": "Select the second image from the user's history",
                "chosen_selector": "@aria-label=Open generation preview",
                "semantic_type": "clickable_item",
                "context_text": "Open generation preview | June 17, 2026",
                "after_url": "https://viggle.ai/app/mix",
                "after_title": "Viggle AI",
                "after_text_excerpt": "History\nBack",
            }
        ],
        success_assertions=[],
    )

    assert "selection/add task clicked a preview-only item instead of a select/use/add action" in issues


def test_validate_capture_success_rejects_missing_my_library_source_selection() -> None:
    issues = validate_capture_success(
        task="在添加图片那里从我的历史库里面选第二张图片",
        captured_actions=[
            {
                "type": "click",
                "target": "Add Image Library button",
                "context_text": "Library | Add Image",
                "after_text_excerpt": "My Library\nFrom Viggle\nTom Aura\nZynpickle",
                "after_url": "https://viggle.ai/app/mix",
                "after_title": "Viggle AI",
            },
            {
                "type": "click",
                "target": "second image in history library",
                "comment": "Select the second image from history",
                "chosen_selector": "@aria-label=Reuse",
                "semantic_type": "button",
                "context_text": "Reuse | Tom Aura",
                "after_url": "https://viggle.ai/app/mix",
                "after_title": "Viggle AI",
            },
        ],
        success_assertions=[],
    )

    assert "task requested My Library but captured workflow did not select or verify My Library" in issues


def test_validate_capture_success_rejects_my_library_target_grounded_to_history() -> None:
    issues = validate_capture_success(
        task="Select the second image from My Library",
        captured_actions=[
            {
                "type": "click",
                "target": "My Library tab",
                "candidate_text": "History",
                "candidate_accessible_name": "History",
                "chosen_selector": "text=History",
                "after_url": "https://example.com",
                "after_title": "Example",
            },
            {
                "type": "click",
                "target": "second image in My Library",
                "chosen_selector": "@aria-label=Reuse",
                "after_url": "https://example.com",
                "after_title": "Example",
            },
        ],
        success_assertions=[],
    )

    assert "task requested My Library but captured workflow did not select or verify My Library" in issues


def test_validate_capture_success_requires_visual_rendering_for_cjk_rich_text() -> None:
    issues = validate_capture_success(
        task="输入标题短视频测试",
        captured_actions=[
            {
                "type": "input",
                "target": "Title text",
                "after_url": "https://example.test/editor",
                "after_title": "Editor",
                "postcondition": {
                    "type": "editable_text_equals",
                    "value": "短视频测试",
                    "requires_visual_text_rendering": True,
                },
                "postcondition_passed": True,
            }
        ],
        success_assertions=[],
    )

    assert (
        "contenteditable CJK text matched DOM but readable visual rendering was not verified"
        in issues
    )

    issues = validate_capture_success(
        task="输入标题短视频测试",
        captured_actions=[
            {
                "type": "input",
                "target": "Title text",
                "after_url": "https://example.test/editor",
                "after_title": "Editor",
                "postcondition": {
                    "type": "editable_text_equals",
                    "value": "短视频测试",
                    "requires_visual_text_rendering": True,
                },
                "postcondition_passed": True,
                "rendered_text_readable": True,
            }
        ],
        success_assertions=[],
    )

    assert issues == []

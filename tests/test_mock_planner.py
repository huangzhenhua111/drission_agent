from __future__ import annotations

from pathlib import Path

import pytest

from app.generation.planner import ActionPlan
from app.generation.planner import ActionStep
from app.generation.planner import LLMPlanner
from app.generation.planner import MockPlanner
from app.generation.planner import build_planner_prompt
from app.generation.planner import build_replanner_prompt
from app.generation.planner import normalize_action_plan
from app.llm.client import LLMJsonResponse


def test_mock_planner_local_search_plan() -> None:
    plan = MockPlanner().plan("打开本地搜索页面，搜索 alpha，并点击第一个结果。")

    assert [step.type for step in plan.steps] == ["goto", "input", "click", "click"]
    assert plan.steps[0].url.startswith("file:///")
    assert plan.steps[1].value == "alpha"
    assert plan.success_assertions == [{"type": "title_contains", "value": "Alpha Detail"}]


def test_mock_planner_local_form_plan() -> None:
    plan = MockPlanner().plan(
        "打开本地表单页面，在姓名输入框输入 Alice，在类型下拉框选择 Two，上传 upload_fixture.txt，然后点击提交。"
    )

    assert [step.type for step in plan.steps] == ["goto", "input", "select", "upload", "click"]
    assert plan.steps[1].value == "Alice"
    assert plan.steps[2].value == "Two"
    assert plan.steps[2].select_by == "text"
    assert plan.steps[3].path.endswith("upload_fixture.txt")


def test_mock_planner_selenium_web_form_plan() -> None:
    plan = MockPlanner().plan(
        "打开 Selenium Web Form 页面，在文本框输入 hello，在下拉框选择 Two，然后点击 Submit。"
    )

    assert [step.type for step in plan.steps] == ["goto", "input", "select", "click"]
    assert plan.steps[0].url == "https://www.selenium.dev/selenium/web/web-form.html"
    assert plan.steps[1].value == "hello"


def test_mock_planner_online_video_upload_plan() -> None:
    plan = MockPlanner().plan(
        "打开 https://online-video-cutter.com/video-editor，上传 /home/huangzhenhua/workspace/drission_agent/outputs/test_assets/sample.mp4 并确认进入时间线。"
    )

    assert [step.type for step in plan.steps] == ["goto", "click", "upload", "wait"]
    assert plan.steps[0].url == "https://online-video-cutter.com/video-editor"
    assert Path(plan.steps[2].path or "").is_file()
    assert plan.steps[3].target == "uploaded file appears in editor timeline"
    assert plan.success_assertions == [{"type": "url_contains", "value": "/projects/"}]


def test_normalize_action_plan_maps_llm_action_aliases() -> None:
    plan = normalize_action_plan(
        {
            "task": "demo",
            "steps": [
                {"type": "navigate", "url": "https://example.com"},
                {"type": "fill", "target": "Search", "value": "alpha"},
                {"type": "choose", "target": "Type", "value": "Two"},
                {"type": "attach_file", "target": "File", "path": "fixture.txt"},
                {"type": "tap", "target": "Submit"},
                {"type": "sleep", "value": "0.1"},
            ],
        }
    )

    assert [step.type for step in plan.steps] == [
        "goto",
        "input",
        "select",
        "upload",
        "click",
        "wait",
    ]
    assert plan.steps[2].select_by == "text"


def test_normalize_action_plan_repairs_missing_upload_target() -> None:
    plan = normalize_action_plan(
        {
            "task": "upload",
            "steps": [
                {
                    "type": "upload",
                    "path": "/tmp/sample.mp4",
                }
            ],
        }
    )

    assert plan.steps[0].target == "File upload input"


def test_planner_prompts_do_not_convert_verification_to_click() -> None:
    prompt = build_planner_prompt("upload a video and confirm it is in the timeline")
    assert "confirm, ensure, verify" in prompt
    assert "Do not convert a verification" in prompt

    replan_prompt = build_replanner_prompt(
        task="confirm the video is in the timeline",
        completed_actions=[],
        failed_step={"type": "click", "target": "Video clip in timeline"},
        current_state={"url": "https://example.test"},
        grounding_candidates=[],
        error="No DOM candidate matched target",
    )
    assert "use a wait step instead of clicking" in replan_prompt
    assert "timeline_media_present=true" in replan_prompt


def test_replanner_prompt_forbids_wait_then_repeat_after_dom_miss() -> None:
    prompt = build_replanner_prompt(
        task="choose Open Sans",
        completed_actions=[],
        failed_step={"type": "click", "target": "Open Sans"},
        current_state={"text_excerpt": "Add text"},
        grounding_candidates=[],
        error="No DOM candidate matched target: Open Sans",
    )

    assert "do not return one or more wait steps followed by the same action and target" in prompt
    assert "runner will use visual fallback" in prompt


def test_replanner_prompt_handles_cjk_visual_text_rendering_failure() -> None:
    prompt = build_replanner_prompt(
        task="replace title with 短视频测试",
        completed_actions=[],
        failed_step={"type": "input", "target": "Title", "value": "短视频测试"},
        current_state={
            "text_excerpt": "Text Open Sans 32 短视频测试",
            "visual_text_check": {
                "readable": False,
                "exact_text_visible": False,
                "issue": "tofu_boxes",
                "reason": "Chinese text is shown as missing-glyph boxes.",
            },
        },
        grounding_candidates=[
            {
                "candidate_id": "font",
                "text": "Open Sans",
                "semantic_type": "clickable_item",
                "action_allowed": ["click"],
            }
        ],
        error="StateNotReachedError: visual_text_check={\"issue\":\"tofu_boxes\"}",
    )

    assert "do not recover by merely clicking the same title/text input" in prompt
    assert "CJK-compatible fonts/styles" in prompt
    assert "missing glyph rendering" in prompt
    assert "double-click the new canvas text box" in prompt
    assert "Never drop this close/commit requirement" in prompt
    assert "do not prepend click Upload/Add files/Open file" in prompt
    assert "Do not assume generic Latin font names" in prompt
    assert "Open Sans" in prompt


def test_planner_prompts_preserve_visible_numeric_business_values() -> None:
    prompt = build_planner_prompt("set speed to 1.5x and opacity to 80%")
    assert "preserve the user's visible business value exactly" in prompt
    assert "guessing an internal slider coordinate" in prompt

    replan_prompt = build_replanner_prompt(
        task="set speed to 1.5x",
        completed_actions=[],
        failed_step={"type": "set_range", "target": "Speed", "value": "1.5"},
        current_state={"url": "https://example.test", "text_excerpt": "Speed 0.1x 1x 16x"},
        grounding_candidates=[
            {
                "candidate_id": "speed",
                "tag": "input",
                "type": "text",
                "value": "1x",
                "context_text": "Speed",
            }
        ],
        error="failed",
    )
    assert "Never convert a requested visible value like 1.5x into a hidden slider coordinate" in replan_prompt


def test_replanner_prompt_compacts_large_runtime_context() -> None:
    prompt = build_replanner_prompt(
        task="edit video",
        completed_actions=[{"type": "goto", "after_url": "https://example.test"}],
        failed_step={"type": "click", "target": "Editor"},
        current_state={
            "url": "https://example.test",
            "title": "Example",
            "text_excerpt": "visible page",
            "html_excerpt": "SECRET-LARGE-HTML" * 100,
        },
        grounding_candidates=[
            {
                "candidate_id": "e1",
                "tag": "button",
                "text": "Editor",
                "selector_metadata": [{"selector": "css:.huge", "match_count": 999}],
            }
        ],
        error="not found",
    )

    assert "visible page" in prompt
    assert "SECRET-LARGE-HTML" not in prompt
    assert "selector_metadata" not in prompt
    assert '\"candidate_id\": \"e1\"' in prompt


def test_normalize_action_plan_rejects_unknown_action_type() -> None:
    with pytest.raises(ValueError, match="Invalid action type"):
        normalize_action_plan(
            ActionPlan(task="demo", steps=[ActionStep("triple_click", target="Submit")])
        )


def test_normalize_action_plan_supports_complex_editor_primitives() -> None:
    plan = normalize_action_plan(
        {
            "task": "edit timeline",
            "steps": [
                {"type": "press_key", "value": "DELETE"},
                {"type": "dblclick", "target": "video card"},
                {"type": "set_slider", "target": "Speed range", "value": "1.5"},
                {"type": "set_time", "target": "Trim start", "value": "00:02.00"},
                {
                    "type": "drag_by",
                    "target": "Trim start handle",
                    "delta_x": 40,
                    "delta_y": 0,
                    "duration": 0.8,
                },
            ],
        }
    )

    assert [step.type for step in plan.steps] == ["press_key", "double_click", "set_range", "set_timecode", "drag"]
    assert plan.steps[4].delta_x == 40.0
    assert plan.steps[4].duration == 0.8


def test_drag_requires_an_explicit_offset() -> None:
    with pytest.raises(ValueError, match="requires delta_x or delta_y"):
        normalize_action_plan(
            {"task": "drag", "steps": [{"type": "drag", "target": "handle"}]}
        )


def test_normalize_action_plan_drops_unstructured_success_assertions() -> None:
    plan = normalize_action_plan(
        {
            "task": "demo",
            "steps": [{"type": "goto", "url": "https://example.com"}],
            "success_assertions": [
                "page worked",
                {"type": "visual", "value": "done"},
                {"type": "title_contains", "value": "Example"},
            ],
        }
    )

    assert plan.success_assertions == [
        {"type": "title_contains", "value": "Example"}
    ]


def test_normalize_action_plan_rejects_missing_required_fields() -> None:
    with pytest.raises(ValueError, match="requires non-empty url"):
        normalize_action_plan(ActionPlan(task="demo", steps=[ActionStep("goto")]))

    with pytest.raises(ValueError, match="requires value"):
        normalize_action_plan(
            ActionPlan(task="demo", steps=[ActionStep("input", target="Search")])
        )

    with pytest.raises(ValueError, match="requires non-empty path"):
        normalize_action_plan(
            ActionPlan(task="demo", steps=[ActionStep("upload", target="File")])
        )


def test_normalize_action_plan_rejects_invalid_select_and_wait_values() -> None:
    with pytest.raises(ValueError, match="select_by"):
        normalize_action_plan(
            ActionPlan(
                task="demo",
                steps=[ActionStep("select", target="Type", value="Two", select_by="label")],
            )
        )

    with pytest.raises(ValueError, match="wait value"):
        normalize_action_plan(ActionPlan(task="demo", steps=[ActionStep("wait", value="soon")]))


def test_normalize_action_plan_repairs_history_library_image_sequence() -> None:
    plan = normalize_action_plan(
        ActionPlan(
            task="打开页面，在添加图片那里从我的历史库里面选第二张图片",
            steps=[
                ActionStep("goto", url="https://example.com"),
                ActionStep("click", target="Add image"),
                ActionStep("click", target="second image in history library"),
            ],
        )
    )

    assert [step.target for step in plan.steps] == [
        None,
        "Add Image Library button",
        "My Library tab",
        "second image in history library",
    ]


def test_normalize_action_plan_removes_redundant_generic_library_open() -> None:
    plan = normalize_action_plan(
        ActionPlan(
            task="Open Mix and select the second image from My Library",
            steps=[
                ActionStep("goto", url="https://example.com"),
                ActionStep("click", target="Add Image Library button"),
                ActionStep("click", target="Library button"),
                ActionStep("click", target="My Library tab"),
                ActionStep("click", target="second image in My Library"),
            ],
        )
    )

    assert [step.target for step in plan.steps] == [
        None,
        "Add Image Library button",
        "My Library tab",
        "second image in My Library",
    ]


def test_normalize_action_plan_removes_duplicate_specific_library_open() -> None:
    plan = normalize_action_plan(
        ActionPlan(
            task="Open Mix and select the second image from My Library",
            steps=[
                ActionStep("goto", url="https://viggle.ai/app/mix"),
                ActionStep("click", target="Add Image Library button"),
                ActionStep(
                    "click",
                    target="Add Image Library button",
                    comment="Open the library within Add Image",
                ),
                ActionStep("click", target="My Library tab"),
                ActionStep("click", target="second image in first row"),
            ],
        )
    )

    assert [step.target for step in plan.steps] == [
        None,
        "Add Image Library button",
        "My Library tab",
        "second image in first row",
    ]


def test_normalize_action_plan_canonicalizes_chinese_add_image_library_target() -> None:
    plan = normalize_action_plan(
        ActionPlan(
            task="从我的历史库选择第二张图片",
            steps=[
                ActionStep("goto", url="https://example.com"),
                ActionStep("click", target="添加图片的库按钮"),
                ActionStep("click", target="My Library tab"),
                ActionStep("click", target="我的历史库中的第二张图片"),
            ],
        )
    )

    assert plan.steps[1].target == "Add Image Library button"


def test_llm_planner_uses_original_task_for_my_library_repairs() -> None:
    client = FakeLLMClient(
        {
            "task": "Open Viggle Mix and add the second image from history library",
            "steps": [
                {"type": "goto", "url": "https://viggle.ai/app/mix"},
                {"type": "click", "target": "Add Image Library button"},
                {"type": "click", "target": "History library tab"},
                {"type": "click", "target": "second image in history library"},
            ],
            "success_assertions": [],
        }
    )

    plan = LLMPlanner(client=client).plan("打开 https://viggle.ai/app，选择 mix 功能，在添加图片那里从我的历史库里面选第二张图片")

    assert plan.task.startswith("打开 https://viggle.ai/app")
    assert [step.target for step in plan.steps] == [
        None,
        "Mix",
        "Add Image Library button",
        "My Library tab",
        "second image in history library",
    ]


class FakeLLMClient:
    def __init__(self, data: dict) -> None:
        self.data = data
        self.last_prompt = ""

    def complete_json(self, *, prompt: str, schema_name: str) -> LLMJsonResponse:
        self.last_prompt = prompt
        return LLMJsonResponse(raw_text="{}", data=self.data)


def test_llm_planner_normalizes_and_validates_llm_json() -> None:
    client = FakeLLMClient(
        {
            "task": "demo",
            "steps": [
                {"type": "navigate", "url": "https://example.com"},
                {"type": "fill", "target": "Search input", "value": "alpha"},
                {"type": "tap", "target": "Search button"},
            ],
            "success_assertions": [],
        }
    )

    plan = LLMPlanner(client=client).plan("open example and search alpha")

    assert [step.type for step in plan.steps] == ["goto", "input", "click"]
    assert "Do not generate CSS selectors" in client.last_prompt


def test_llm_planner_replaces_unverified_deep_link_with_visible_navigation() -> None:
    client = FakeLLMClient(
        {
            "task": "open editor",
            "steps": [
                {
                    "type": "goto",
                    "target": "123Apps video editor",
                    "url": "https://123apps.com/video-editor/",
                },
                {"type": "upload", "target": "Open file", "path": "/tmp/sample.mp4"},
            ],
            "success_assertions": [],
        }
    )

    plan = LLMPlanner(client=client).plan("打开 123Apps 视频编辑器并上传 /tmp/sample.mp4")

    assert [step.type for step in plan.steps] == ["goto", "click", "upload"]
    assert plan.steps[0].url == "https://123apps.com/"
    assert plan.steps[1].target == "Video Editor"


def test_llm_planner_keeps_exact_user_supplied_deep_link() -> None:
    client = FakeLLMClient(
        {
            "task": "open editor",
            "steps": [{"type": "goto", "url": "https://example.test/tools/editor"}],
            "success_assertions": [],
        }
    )

    plan = LLMPlanner(client=client).plan("打开 https://example.test/tools/editor")

    assert len(plan.steps) == 1
    assert plan.steps[0].url == "https://example.test/tools/editor"


def test_llm_planner_uses_user_supplied_parent_url_before_feature_click() -> None:
    client = FakeLLMClient(
        {
            "task": "open mix",
            "steps": [{"type": "goto", "url": "https://viggle.ai/app/mix"}],
            "success_assertions": [],
        }
    )

    plan = LLMPlanner(client=client).plan("打开 https://viggle.ai/app 并选择 Mix")

    assert [step.type for step in plan.steps] == ["goto", "click"]
    assert plan.steps[0].url == "https://viggle.ai/app"
    assert plan.steps[1].target == "Mix"


def test_llm_planner_reports_invalid_llm_plan_with_raw_response() -> None:
    client = FakeLLMClient(
        {
            "task": "demo",
            "steps": [{"type": "hover", "target": "menu"}],
        }
    )

    with pytest.raises(ValueError, match="invalid ActionPlan"):
        LLMPlanner(client=client).plan("hover menu")

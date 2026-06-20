from __future__ import annotations

import pytest

from app.generation.planner import ActionPlan
from app.generation.planner import ActionStep
from app.generation.planner import LLMPlanner
from app.generation.planner import MockPlanner
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


def test_normalize_action_plan_rejects_unknown_action_type() -> None:
    with pytest.raises(ValueError, match="Invalid action type"):
        normalize_action_plan(
            ActionPlan(task="demo", steps=[ActionStep("double_click", target="Submit")])
        )


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


def test_llm_planner_reports_invalid_llm_plan_with_raw_response() -> None:
    client = FakeLLMClient(
        {
            "task": "demo",
            "steps": [{"type": "hover", "target": "menu"}],
        }
    )

    with pytest.raises(ValueError, match="invalid ActionPlan"):
        LLMPlanner(client=client).plan("hover menu")

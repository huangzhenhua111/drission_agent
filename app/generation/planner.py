from __future__ import annotations

from dataclasses import asdict
from dataclasses import dataclass, field
from pathlib import Path

from app.llm.client import LLMClient
from app.llm.client import OpenAIJsonClient


VALID_ACTION_TYPES = {"goto", "click", "input", "select", "upload", "wait"}
ACTION_TYPE_ALIASES = {
    "open": "goto",
    "navigate": "goto",
    "visit": "goto",
    "go": "goto",
    "go_to": "goto",
    "tap": "click",
    "press": "click",
    "submit": "click",
    "fill": "input",
    "type": "input",
    "type_text": "input",
    "enter": "input",
    "enter_text": "input",
    "set_text": "input",
    "choose": "select",
    "dropdown": "select",
    "select_option": "select",
    "file_upload": "upload",
    "upload_file": "upload",
    "attach": "upload",
    "attach_file": "upload",
    "sleep": "wait",
    "pause": "wait",
}
VALID_SELECT_BY = {"text", "value", "index"}


@dataclass(frozen=True)
class ActionStep:
    type: str
    target: str | None = None
    value: str | None = None
    url: str | None = None
    path: str | None = None
    select_by: str | None = None
    comment: str | None = None

    def to_dict(self) -> dict:
        return {key: value for key, value in asdict(self).items() if value is not None}


@dataclass(frozen=True)
class ActionPlan:
    task: str
    steps: list[ActionStep] = field(default_factory=list)
    success_assertions: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "task": self.task,
            "steps": [step.to_dict() for step in self.steps],
            "success_assertions": list(self.success_assertions),
        }


class Planner:
    def plan(self, task: str) -> ActionPlan:
        raise NotImplementedError("Planner implementation lands in the next phase.")

    def replan(
        self,
        *,
        task: str,
        completed_actions: list[dict],
        failed_step: dict,
        current_state: dict,
        grounding_candidates: list[dict],
        error: str,
    ) -> ActionPlan:
        raise NotImplementedError("Planner does not support replanning.")


def normalize_action_type(action_type: str) -> str:
    normalized = (action_type or "").strip().lower().replace("-", "_").replace(" ", "_")
    return ACTION_TYPE_ALIASES.get(normalized, normalized)


def normalize_action_step(step: ActionStep | dict) -> ActionStep:
    data = step.to_dict() if isinstance(step, ActionStep) else dict(step)
    action_type = normalize_action_type(str(data.get("type") or ""))
    select_by = data.get("select_by")
    if action_type == "select" and not select_by:
        select_by = "text"
    if isinstance(select_by, str):
        select_by = select_by.strip().lower()
    return ActionStep(
        type=action_type,
        target=_clean_optional_text(data.get("target")),
        value=_clean_optional_text(data.get("value")),
        url=_clean_optional_text(data.get("url")),
        path=_clean_optional_text(data.get("path")),
        select_by=select_by,
        comment=_clean_optional_text(data.get("comment")),
    )


def normalize_action_plan(plan: ActionPlan | dict) -> ActionPlan:
    if isinstance(plan, ActionPlan):
        task = plan.task
        raw_steps = plan.steps
        success_assertions = plan.success_assertions
    else:
        task = str(plan.get("task") or "")
        raw_steps = plan.get("steps") or []
        success_assertions = plan.get("success_assertions") or []

    steps = [normalize_action_step(step) for step in raw_steps]
    steps = _repair_common_step_sequences(task, steps)
    normalized = ActionPlan(
        task=task,
        steps=steps,
        success_assertions=list(success_assertions),
    )
    validate_action_plan(normalized)
    return normalized


def validate_action_plan(plan: ActionPlan) -> None:
    if not plan.steps:
        raise ValueError("ActionPlan must contain at least one step.")
    for index, step in enumerate(plan.steps):
        _validate_action_step(step, index)


def _validate_action_step(step: ActionStep, index: int) -> None:
    if step.type not in VALID_ACTION_TYPES:
        raise ValueError(
            f"Invalid action type at step {index}: {step.type!r}. "
            f"Allowed types: {sorted(VALID_ACTION_TYPES)}"
        )
    if step.type == "goto":
        _require_field(step.url, index, "url")
        return
    if step.type == "wait":
        if step.value is not None:
            try:
                float(step.value)
            except ValueError as exc:
                raise ValueError(f"Step {index} wait value must be seconds.") from exc
        return

    _require_field(step.target, index, "target")
    if step.type == "input":
        _require_not_none(step.value, index, "value")
    elif step.type == "select":
        _require_field(step.value, index, "value")
        if step.select_by not in VALID_SELECT_BY:
            raise ValueError(
                f"Step {index} select_by must be one of {sorted(VALID_SELECT_BY)}."
            )
    elif step.type == "upload":
        _require_field(step.path, index, "path")


def _clean_optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text if text else None


def _require_field(value: str | None, step_index: int, field_name: str) -> None:
    if not value:
        raise ValueError(f"Step {step_index} requires non-empty {field_name}.")


def _require_not_none(value: object, step_index: int, field_name: str) -> None:
    if value is None:
        raise ValueError(f"Step {step_index} requires {field_name}.")


def _repair_common_step_sequences(task: str, steps: list[ActionStep]) -> list[ActionStep]:
    task_lower = task.lower()
    if not _mentions_history_library_image(task_lower):
        return _remove_redundant_library_open_steps(steps)

    wants_user_library = _mentions_user_library(task_lower)
    repaired = [
        _repair_history_library_open_step(step, wants_user_library=wants_user_library)
        for step in steps
    ]
    repaired = _remove_redundant_library_open_steps(repaired)
    second_index = _find_history_item_step_index(repaired)
    if second_index is None:
        return repaired

    preceding_text = " ".join(
        " ".join(str(value or "") for value in [step.target, step.comment]).lower()
        for step in repaired[:second_index]
    )
    inserts: list[ActionStep] = []
    if "library" not in preceding_text and "历史库" not in preceding_text:
        inserts.append(
            ActionStep(
                "click",
                target="Add Image Library button",
                comment="Open the image library for the Add Image area",
            )
        )
    if wants_user_library:
        if not _mentions_my_library(preceding_text):
            inserts.append(
                ActionStep(
                    "click",
                    target="My Library tab",
                    comment="Switch to the user's own library",
                )
            )
    elif "history" not in preceding_text and "历史" not in preceding_text:
        inserts.append(ActionStep("click", target="History", comment="Open history library"))
    if inserts:
        repaired[second_index:second_index] = inserts
    return repaired


def _remove_redundant_library_open_steps(steps: list[ActionStep]) -> list[ActionStep]:
    repaired: list[ActionStep] = []
    library_opened_for: str | None = None
    for step in steps:
        if step.type != "click":
            repaired.append(step)
            continue

        blob = " ".join(str(value or "") for value in [step.target, step.comment]).lower()
        if "add image" in blob and "library" in blob:
            library_opened_for = "image"
            repaired.append(step)
            continue
        if "add motion" in blob and "library" in blob:
            library_opened_for = "motion"
            repaired.append(step)
            continue

        target = " ".join(str(step.target or "").lower().split())
        if library_opened_for and target in {
            "library",
            "library button",
            "open library",
            "open library button",
        }:
            continue

        repaired.append(step)
    return repaired


def _repair_history_library_open_step(step: ActionStep, *, wants_user_library: bool = False) -> ActionStep:
    if step.type != "click":
        return step
    blob = " ".join(str(value or "") for value in [step.target, step.comment]).lower()
    if wants_user_library and _looks_like_history_library_tab(blob):
        return ActionStep(
            "click",
            target="My Library tab",
            comment=step.comment or "Switch to the user's own library",
        )
    mentions_add_image = (
        "add image" in blob
        or "添加图片" in blob
        or "增加图片" in blob
        or "图片按钮" in blob
    )
    mentions_library = "library" in blob or "历史库" in blob or "库" in blob
    if mentions_add_image:
        return ActionStep(
            "click",
            target="Add Image Library button",
            comment=step.comment or "Open the image library for the Add Image area",
        )
    return step


def _mentions_history_library_image(text: str) -> bool:
    return (
        ("history" in text or "历史" in text or _mentions_user_library(text))
        and ("library" in text or "库" in text)
        and ("image" in text or "图片" in text or "图" in text)
    )


def _mentions_user_library(text: str) -> bool:
    return (
        "my library" in text
        or "my history library" in text
        or "user library" in text
        or "personal library" in text
        or "自己的库" in text
        or "我的库" in text
        or "我的历史库" in text
        or ("我的" in text and "库" in text)
    )


def _mentions_my_library(text: str) -> bool:
    return (
        "my library" in text
        or "my history library" in text
        or "user library" in text
        or "personal library" in text
        or "我的库" in text
        or "我的历史库" in text
    )


def _looks_like_history_library_tab(text: str) -> bool:
    return (
        ("history" in text or "历史" in text)
        and ("library" in text or "库" in text)
        and ("tab" in text or "标签" in text or "切换" in text or "switch" in text)
    )


def _find_history_item_step_index(steps: list[ActionStep]) -> int | None:
    for index, step in enumerate(steps):
        blob = " ".join(str(value or "") for value in [step.target, step.comment]).lower()
        mentions_second = "second" in blob or "第二" in blob or "第2" in blob
        mentions_media = "image" in blob or "picture" in blob or "图片" in blob or "图" in blob
        mentions_history = "history" in blob or "历史" in blob or "library" in blob or "库" in blob
        if mentions_second and mentions_media and mentions_history:
            return index
    return None


class MockPlanner(Planner):
    """Deterministic planner for local demos and tests before LLM integration."""

    def __init__(self, project_root: Path | None = None) -> None:
        self.project_root = project_root or Path(__file__).resolve().parents[2]

    def plan(self, task: str) -> ActionPlan:
        normalized = task.lower()
        if "local_search" in normalized or "本地搜索" in task or "alpha" in normalized:
            return normalize_action_plan(self._local_search_plan(task))
        if "local_form" in normalized or "本地表单" in task or "alice" in normalized:
            return normalize_action_plan(self._local_form_plan(task))
        if "selenium" in normalized or "web form" in normalized:
            return normalize_action_plan(self._selenium_web_form_plan(task))
        raise ValueError(
            "MockPlanner only supports local_search, local_form, and Selenium Web Form tasks."
        )

    def _local_search_plan(self, task: str) -> ActionPlan:
        url = (self.project_root / "examples/local_search/site/index.html").resolve().as_uri()
        return ActionPlan(
            task=task,
            steps=[
                ActionStep("goto", url=url, target="本地搜索页面", comment="打开本地搜索页面"),
                ActionStep(
                    "input",
                    target="Search keyword 搜索输入框",
                    value="alpha",
                    comment="输入 alpha",
                ),
                ActionStep("click", target="Search 按钮", comment="点击搜索按钮"),
                ActionStep("click", target="第一条搜索结果 Alpha detail", comment="点击第一条结果"),
            ],
            success_assertions=[{"type": "title_contains", "value": "Alpha Detail"}],
        )

    def _local_form_plan(self, task: str) -> ActionPlan:
        site_dir = self.project_root / "examples/local_form/site"
        return ActionPlan(
            task=task,
            steps=[
                ActionStep(
                    "goto",
                    url=(site_dir / "index.html").resolve().as_uri(),
                    target="本地表单页面",
                    comment="打开本地表单页面",
                ),
                ActionStep("input", target="Name 姓名输入框", value="Alice", comment="输入 Alice"),
                ActionStep(
                    "select",
                    target="Type 类型下拉框",
                    value="Two",
                    select_by="text",
                    comment="选择 Two",
                ),
                ActionStep(
                    "upload",
                    target="Upload file 文件上传框",
                    path=str((site_dir / "upload_fixture.txt").resolve()),
                    comment="上传 fixture 文件",
                ),
                ActionStep("click", target="Submit 按钮", comment="点击提交"),
            ],
            success_assertions=[{"type": "title_contains", "value": "Form Submitted"}],
        )

    def _selenium_web_form_plan(self, task: str) -> ActionPlan:
        return ActionPlan(
            task=task,
            steps=[
                ActionStep(
                    "goto",
                    url="https://www.selenium.dev/selenium/web/web-form.html",
                    target="Selenium Web Form 页面",
                    comment="打开 Selenium Web Form 页面",
                ),
                ActionStep(
                    "input",
                    target="Text input 文本框",
                    value="hello",
                    comment="输入 hello",
                ),
                ActionStep(
                    "select",
                    target="Dropdown select 下拉框",
                    value="Two",
                    select_by="text",
                    comment="选择 Two",
                ),
                ActionStep("click", target="Submit 按钮", comment="点击提交"),
            ],
            success_assertions=[{"type": "url_contains", "value": "submitted-form.html"}],
        )


class LLMPlanner(Planner):
    def __init__(self, client: LLMClient | None = None) -> None:
        self.client = client or OpenAIJsonClient()

    def plan(self, task: str) -> ActionPlan:
        prompt = build_planner_prompt(task)
        response = self.client.complete_json(prompt=prompt, schema_name="ActionPlan")
        try:
            data = dict(response.data)
            data["task"] = task
            return normalize_action_plan(data)
        except ValueError as exc:
            raise ValueError(
                f"LLMPlanner returned an invalid ActionPlan: {exc}. Raw response: {response.raw_text}"
            ) from exc

    def replan(
        self,
        *,
        task: str,
        completed_actions: list[dict],
        failed_step: dict,
        current_state: dict,
        grounding_candidates: list[dict],
        error: str,
    ) -> ActionPlan:
        prompt = build_replanner_prompt(
            task=task,
            completed_actions=completed_actions,
            failed_step=failed_step,
            current_state=current_state,
            grounding_candidates=grounding_candidates,
            error=error,
        )
        response = self.client.complete_json(prompt=prompt, schema_name="ActionPlan")
        try:
            data = dict(response.data)
            data["task"] = task
            return normalize_action_plan(data)
        except ValueError as exc:
            raise ValueError(
                f"LLMPlanner returned an invalid replan ActionPlan: {exc}. Raw response: {response.raw_text}"
            ) from exc


def build_planner_prompt(task: str) -> str:
    return f"""Convert the user task into a browser automation ActionPlan JSON object.

Task:
{task}

Return exactly this JSON shape:
{{
  "task": "...",
  "steps": [
    {{
      "type": "goto | click | input | select | upload | wait",
      "target": "human-readable target, required except goto/wait",
      "url": "required for goto",
      "value": "required for input/select; optional seconds for wait",
      "path": "required for upload",
      "select_by": "text | value | index, optional for select and defaults to text",
      "comment": "short explanation"
    }}
  ],
  "success_assertions": []
}}

Rules:
- Use only these action types: goto, click, input, select, upload, wait.
- Do not generate CSS selectors, XPath, DOM indexes, or code.
- Use goto for explicit URLs.
- Use input for text entry into inputs or textareas.
- Use select for dropdown choices.
- Use upload only when a local file path is requested.
- Use click for buttons, links, tabs, result items, and submit actions.
- Use wait only when the task explicitly asks to wait.
- Keep targets semantic and close to user wording, for example "Search input", "Submit button", "first search result".
- For select, include select_by "text" unless the user clearly asks for value or index.
- If the task says to choose an image/video/file from a history/library inside an upload area, split it into separate click steps:
  1. click the specific upload area's Library button, for example "Add Image Library button";
  2. click the exact source tab named by the user, for example "My Library tab" for "my library / 我的历史库", or "History" only when the visible source is actually named History;
  3. click the requested item, for example "second image in history library".
- Preserve user-owned source wording. Do not rewrite "my library", "my history library", "我的库", or "我的历史库" into a generic "History" tab.
- If the task opens a web app and then selects a named feature with an obvious route in the same URL, prefer a direct goto to that feature route when safe, for example Viggle Mix is https://viggle.ai/app/mix.
- For ambiguous tasks, produce the smallest reasonable step list.
"""


def build_replanner_prompt(
    *,
    task: str,
    completed_actions: list[dict],
    failed_step: dict,
    current_state: dict,
    grounding_candidates: list[dict],
    error: str,
) -> str:
    return f"""The browser automation plan no longer matches the current page.

Original task:
{task}

Completed actions JSON:
{completed_actions}

Failed original step JSON:
{failed_step}

Current page state JSON:
{current_state}

Current action-specific grounding candidates JSON:
{grounding_candidates[:40]}

Execution error:
{error}

Return a replacement ActionPlan JSON object for ONLY the remaining work from the current page.

Use exactly this JSON shape:
{{
  "task": "...",
  "steps": [
    {{
      "type": "goto | click | input | select | upload | wait",
      "target": "human-readable target, required except goto/wait",
      "url": "required for goto",
      "value": "required for input/select; optional seconds for wait",
      "path": "required for upload",
      "select_by": "text | value | index, optional for select and defaults to text",
      "comment": "short explanation"
    }}
  ],
  "success_assertions": []
}}

Rules:
- Do not repeat actions already completed unless the current page state proves they are needed again.
- Do not generate selectors, XPath, DOM indexes, or code.
- Prefer steps that can be grounded against the provided current candidates.
- If the current page is a login/auth page, do not replan login. Return the smallest non-login remaining plan after login.
- If the task says to choose an image/video/file from history/library, use click steps for the relevant Library button, History tab, and requested item.
- If the original task says "my library", "my history library", "我的库", or "我的历史库", use "My Library tab" as the source tab, not "History" or "From Viggle".
- Keep targets semantic and close to visible page text.
"""

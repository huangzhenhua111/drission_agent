from __future__ import annotations

import json
import re
from dataclasses import asdict
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse
from urllib.parse import urlunparse

from app.llm.client import LLMClient
from app.llm.client import OpenAIJsonClient


VALID_ACTION_TYPES = {
    "goto", "click", "double_click", "input", "select", "upload", "wait",
    "press_key", "set_range", "set_timecode", "drag",
}
ACTION_TYPE_ALIASES = {
    "open": "goto",
    "navigate": "goto",
    "visit": "goto",
    "go": "goto",
    "go_to": "goto",
    "tap": "click",
    "dblclick": "double_click",
    "doubleclick": "double_click",
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
    "key_press": "press_key",
    "keyboard": "press_key",
    "set_slider": "set_range",
    "adjust_range": "set_range",
    "set_time": "set_timecode",
    "set_time_code": "set_timecode",
    "timecode": "set_timecode",
    "drag_by": "drag",
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
    delta_x: float | None = None
    delta_y: float | None = None
    duration: float | None = None

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
    target = _clean_optional_text(data.get("target"))
    if action_type == "upload" and not target and _clean_optional_text(data.get("path")):
        target = "File upload input"
    return ActionStep(
        type=action_type,
        target=target,
        value=_clean_optional_text(data.get("value")),
        url=_clean_optional_text(data.get("url")),
        path=_clean_optional_text(data.get("path")),
        select_by=select_by,
        comment=_clean_optional_text(data.get("comment")),
        delta_x=_clean_optional_float(data.get("delta_x")),
        delta_y=_clean_optional_float(data.get("delta_y")),
        duration=_clean_optional_float(data.get("duration")),
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
        success_assertions=_normalize_success_assertions(success_assertions),
    )
    validate_action_plan(normalized)
    return normalized


def _normalize_success_assertions(assertions: object) -> list[dict]:
    if not isinstance(assertions, list):
        return []
    normalized: list[dict] = []
    for assertion in assertions:
        if not isinstance(assertion, dict):
            continue
        assertion_type = assertion.get("type")
        value = assertion.get("value")
        if assertion_type not in {"url_contains", "title_contains"} or not value:
            continue
        normalized.append({"type": assertion_type, "value": str(value)})
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
    if step.type == "press_key":
        _require_field(step.value, index, "value")
        return

    _require_field(step.target, index, "target")
    if step.type == "input":
        _require_not_none(step.value, index, "value")
    elif step.type in {"set_range", "set_timecode"}:
        _require_not_none(step.value, index, "value")
    elif step.type == "drag":
        if step.delta_x is None and step.delta_y is None:
            raise ValueError(f"Step {index} drag requires delta_x or delta_y.")
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


def _clean_optional_float(value: object) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Expected a numeric action field, got {value!r}.") from exc


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
            if library_opened_for == "image":
                continue
            library_opened_for = "image"
            repaired.append(step)
            continue
        if "add motion" in blob and "library" in blob:
            if library_opened_for == "motion":
                continue
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
        if (
            "online-video-cutter" in normalized
            or "123apps" in normalized
            or "sample.mp4" in normalized
        ):
            return normalize_action_plan(self._online_video_upload_plan(task))
        raise ValueError(
            "MockPlanner only supports local_search, local_form, Selenium Web Form, "
            "and 123Apps upload tasks."
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

    def _online_video_upload_plan(self, task: str) -> ActionPlan:
        upload_path = _first_file_path_from_text(task)
        if upload_path is None:
            upload_path = self.project_root / "outputs" / "test_assets" / "sample.mp4"
        return ActionPlan(
            task=task,
            steps=[
                ActionStep(
                    "goto",
                    url="https://online-video-cutter.com/video-editor",
                    target="Online Video Cutter video editor",
                    comment="Open the online video editor",
                ),
                ActionStep(
                    "click",
                    target="Create Project button",
                    comment="Create a new video editing project",
                ),
                ActionStep(
                    "upload",
                    target="Video upload area",
                    path=str(upload_path.resolve()),
                    comment="Upload the requested video file",
                ),
                ActionStep(
                    "wait",
                    target="uploaded file appears in editor timeline",
                    value="3",
                    comment="Verify the uploaded video appears in the editor timeline",
                ),
            ],
            success_assertions=[{"type": "url_contains", "value": "/projects/"}],
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
            plan = normalize_action_plan(data)
            return _repair_unverified_deep_links(task, plan)
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
      "type": "goto | click | double_click | input | select | upload | wait | press_key | set_range | set_timecode | drag",
      "target": "human-readable target, required except goto/wait",
      "url": "required for goto",
      "value": "required for input/select; optional seconds for wait",
      "path": "required for upload",
      "select_by": "text | value | index, optional for select and defaults to text",
      "delta_x": "horizontal pixel offset required for drag unless delta_y is present",
      "delta_y": "vertical pixel offset required for drag unless delta_x is present",
      "duration": "optional drag duration in seconds",
      "comment": "short explanation"
    }}
  ],
  "success_assertions": []
}}

Rules:
- Use only these action types: goto, click, double_click, input, select, upload, wait, press_key, set_range, set_timecode, drag.
- Do not generate CSS selectors, XPath, DOM indexes, or code.
- Use goto for explicit URLs.
- Use input for text entry into inputs or textareas.
- Use select for dropdown choices.
- Use upload only when a local file path is requested.
- When the task provides a concrete local file path, plan upload directly against the editor's file input/drop zone. Do not add a preceding click on Upload, Add files, Open file, or a media-library tab; the upload action itself must set that local file on the file input. After upload, verify the timeline before considering any library-card double-click.
- Use click for buttons, links, tabs, result items, and submit actions.
- Use double_click when an existing media/file card must be inserted into a timeline and a single click would only select it.
- Use press_key for keyboard commands such as Delete after the intended item is selected; put the key name in value.
- Use set_range for a real input[type=range] or numeric slider whose requested value is known.
- For Speed, Opacity, Volume, scale, percent, or multiplier controls, preserve the user's visible business value exactly, such as 1.5 or 80. Prefer a visible numeric/text value field paired with that control over guessing an internal slider coordinate or logarithmic mapping.
- Use set_timecode for a segmented/composite time control; put a value such as 00:02.00 in value and identify start or end in target.
- In a video/image editor, opening a Text tool may show text styles or presets rather than an input. Click a visible text style/preset to create the text object first. If a normal input/contenteditable then appears, input into it. Otherwise double-click the newly created text box on the preview canvas, use press_key with Control+A, and input the replacement text into the focused canvas text editor.
- Treat the canvas text box and the text-property panel as different controls: the canvas object is edited by double-click/keyboard, while font, size, color, alignment, stroke, background, shadow, and opacity normally belong to the property panel. Do not type into a font selector.
- After editing a canvas text object, commit it by moving focus to a safe property control or outside the text editor, then verify the rendered preview/timeline text. DOM text alone is insufficient for rich canvas editors.
- Use drag only when a handle/clip must move by an explicit pixel offset; include delta_x/delta_y and optional duration.
- Use wait when the task asks to wait, confirm, ensure, verify, or observe that something is loaded/present. Do not convert a verification like "confirm the video is in the timeline" into a click unless the user explicitly asks to select/click that item.
- Keep targets semantic and close to user wording, for example "Search input", "Submit button", "first search result".
- For select, include select_by "text" unless the user clearly asks for value or index.
- If the task says to choose an image/video/file from a history/library inside an upload area, split it into separate click steps:
  1. click the specific upload area's Library button, for example "Add Image Library button";
  2. click the exact source tab named by the user, for example "My Library tab" for "my library / 我的历史库", or "History" only when the visible source is actually named History;
  3. click the requested item, for example "second image in history library".
- Preserve user-owned source wording. Do not rewrite "my library", "my history library", "我的库", or "我的历史库" into a generic "History" tab.
- Never invent or guess a deep-link path that the user did not provide. If the task names a site and a feature but gives no exact feature URL, goto the site root (or the exact URL supplied by the user) and click the visible feature entry. The browser's resulting URL is authoritative.
- For ambiguous tasks, produce the smallest reasonable step list.
- success_assertions must contain only objects with type "url_contains" or "title_contains" and a string value. Use an empty list when no reliable URL/title assertion is known; never return assertion strings.
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
    compact_actions = _compact_completed_actions(completed_actions)
    compact_state = _compact_current_state(current_state)
    compact_candidates = _compact_grounding_candidates(grounding_candidates)
    return f"""The browser automation plan no longer matches the current page.

Original task:
{task}

Completed actions JSON:
{json.dumps(compact_actions, ensure_ascii=False)}

Failed original step JSON:
{failed_step}

Current page state JSON:
{json.dumps(compact_state, ensure_ascii=False)}

Current action-specific grounding candidates JSON:
{json.dumps(compact_candidates, ensure_ascii=False)}

Execution error:
{error}

Return a replacement ActionPlan JSON object for ONLY the remaining work from the current page.

Use exactly this JSON shape:
{{
  "task": "...",
  "steps": [
    {{
      "type": "goto | click | double_click | input | select | upload | wait | press_key | set_range | set_timecode | drag",
      "target": "human-readable target, required except goto/wait",
      "url": "required for goto",
      "value": "required for input/select; optional seconds for wait",
      "path": "required for upload",
      "select_by": "text | value | index, optional for select and defaults to text",
      "delta_x": "horizontal pixel offset for drag",
      "delta_y": "vertical pixel offset for drag",
      "duration": "optional drag duration in seconds",
      "comment": "short explanation"
    }}
  ],
  "success_assertions": []
}}

Rules:
- Do not repeat actions already completed unless the current page state proves they are needed again.
- Do not generate selectors, XPath, DOM indexes, or code.
- Prefer steps that can be grounded against the provided current candidates.
- When the execution error says no DOM candidate matched the failed target, do not return one or more wait steps followed by the same action and target. Choose a different candidate-backed route; if none exists, do not disguise the unchanged failed action as a recovery because the runner will use visual fallback.
- If a media/file card is selected but the timeline remains empty, use double_click on that card to insert it before trying editor controls.
- Treat current_state.timeline_media_present=true as authoritative: media is already in the timeline. Never upload, click, or double-click a library asset in that case; retry the requested canvas/property action directly.
- If the current state already exposes two or more media property controls such as Trim, Speed, Opacity, or Crop, the media object is selected. Do not add or reselect the library asset.
- When the original task provides a local file path and the editor timeline is still empty, prefer an upload step with that original path over guessing single/double-click behavior on a library card.
- For a concrete local path, do not prepend click Upload/Add files/Open file or switch to a media library. Use upload directly on the current file input/drop zone, then verify timeline_media_present; do not double-click the uploaded library card if upload already inserted it into the timeline.
- Use set_timecode, not input, when a candidate has semantic_type composite_time_input; identify start/end in target and use a value such as 00:02.00.
- For Speed, Opacity, Volume, scale, percent, or multiplier controls, preserve the user's visible business value exactly, such as 1.5 or 80. Prefer a visible numeric/text value field paired with that control over guessing an internal slider coordinate or logarithmic mapping.
- Never convert a requested visible value like 1.5x into a hidden slider coordinate unless the current candidates prove there is no visible value field and expose the slider's exact public min/max/value contract.
- If the Text panel shows style/font presets and no title/text input candidate, click a visible preset to create a text object. If no normal input is exposed after creation, double-click the new canvas text box, press Control+A, and input into the focused contenteditable; then move focus outside the editor and verify the rendered preview/timeline text.
- Treat the canvas text object and the property panel separately. Double-click/keyboard edits text content; font, size, color, alignment, stroke, background, shadow, and opacity are property controls. Never mistake a font-name button such as Open Sans for the text input.
- Property controls such as color/font pickers may remain open after selecting a value and can cover the preview. Before a wait or visual verification, explicitly close/dismiss the temporary picker (Escape, its close control, or a safe click outside) and commit the canvas edit. Never drop this close/commit requirement when replacing the remaining plan.
- If current_state.visual_text_check or the execution error reports issue "tofu_boxes", "unreadable", "missing", or "wrong_text" after an input step, do not recover by merely clicking the same title/text input or retyping the same DOM text. First distinguish an uncommitted canvas edit from missing glyph rendering: focus/commit the canvas text editor and verify again. If glyph boxes remain, use explicitly available CJK-compatible fonts/styles; if none is visible, report the rendering/environment limitation instead of inventing a font option.
- For CJK text rendering failures, prefer visible font/style controls such as the current font dropdown (for example "Open Sans") and choose a font/style that explicitly indicates CJK, Chinese, Simplified Chinese, Japanese, Korean, Noto Sans CJK/SC, Source Han, Microsoft YaHei, SimHei, PingFang, or another available CJK-capable option.
- Do not assume generic Latin font names such as Open Sans, Noto Sans, Noto Serif, Roboto, Lora, Oswald, or Cormorant SC support Chinese just because they share part of a CJK font family name. The visible font option itself must explicitly indicate CJK/Chinese/SC/TC/JP/KR or a known CJK font name; otherwise report or verify failure rather than cycling through likely Latin fonts.
- If the remaining work is to confirm/ensure/verify that content is present or loaded, use a wait step instead of clicking a vaguely named content area.
- If the current page is a login/auth page, do not replan login. Return the smallest non-login remaining plan after login.
- If the task says to choose an image/video/file from history/library, use click steps for the relevant Library button, History tab, and requested item.
- If the original task says "my library", "my history library", "我的库", or "我的历史库", use "My Library tab" as the source tab, not "History" or "From Viggle".
- Keep targets semantic and close to visible page text.
- Never plan clicks on CAPTCHA, Cloudflare Turnstile, "Verify you are human", or other anti-bot security challenges. Those require manual completion in headed mode. Ordinary application buttons unrelated to anti-bot checks may still be planned normally.
- success_assertions must contain only objects with type "url_contains" or "title_contains" and a string value. Use an empty list when no reliable URL/title assertion is known; never return assertion strings.
"""


def _compact_completed_actions(actions: list[dict]) -> list[dict]:
    keys = (
        "type", "target", "url", "value", "path", "chosen_selector",
        "after_url", "after_title", "candidate_text", "candidate_accessible_name",
    )
    return [
        {key: action.get(key) for key in keys if action.get(key) is not None}
        for action in actions[-20:]
    ]


def _repair_unverified_deep_links(task: str, plan: ActionPlan) -> ActionPlan:
    explicit_urls = _urls_in_task(task)
    repaired: list[ActionStep] = []
    for step in plan.steps:
        if step.type != "goto" or not step.url:
            repaired.append(step)
            continue
        parsed = urlparse(step.url)
        if not parsed.scheme or not parsed.netloc or parsed.path in {"", "/"}:
            repaired.append(step)
            continue
        if any(_same_url(step.url, explicit) for explicit in explicit_urls):
            repaired.append(step)
            continue

        same_origin_explicit = [
            explicit
            for explicit in explicit_urls
            if _same_origin(step.url, explicit)
            and parsed.path.startswith(urlparse(explicit).path.rstrip("/") + "/")
        ]
        if same_origin_explicit:
            safe_url = max(same_origin_explicit, key=lambda value: len(urlparse(value).path))
        else:
            safe_url = urlunparse((parsed.scheme, parsed.netloc, "/", "", "", ""))

        feature_target = _deep_link_feature_target(step.url, safe_url)
        repaired.append(
            ActionStep(
                "goto",
                target=step.target,
                url=safe_url,
                comment="Open verified site entry before selecting the requested feature",
            )
        )
        if feature_target:
            repaired.append(
                ActionStep(
                    "click",
                    target=feature_target,
                    comment="Open requested feature from the visible site navigation",
                )
            )
    return ActionPlan(
        task=plan.task,
        steps=repaired,
        success_assertions=plan.success_assertions,
    )


def _urls_in_task(task: str) -> list[str]:
    return re.findall(r"https?://[^\s，,。；;）)\]}>\"']+", str(task), flags=re.IGNORECASE)


def _first_file_path_from_text(text: str) -> Path | None:
    for match in re.findall(r"(/[^\s，,。；;）)\]}>\"']+)", text):
        path = Path(match).expanduser()
        if path.exists() and path.is_file():
            return path
    return None


def _same_origin(left: str, right: str) -> bool:
    left_parsed = urlparse(left)
    right_parsed = urlparse(right)
    return (left_parsed.scheme.lower(), left_parsed.netloc.lower()) == (
        right_parsed.scheme.lower(), right_parsed.netloc.lower()
    )


def _same_url(left: str, right: str) -> bool:
    return left.rstrip("/") == right.rstrip("/")


def _deep_link_feature_target(planned_url: str, safe_url: str) -> str | None:
    planned_parts = [part for part in urlparse(planned_url).path.split("/") if part]
    safe_parts = [part for part in urlparse(safe_url).path.split("/") if part]
    remaining = planned_parts[len(safe_parts):]
    if not remaining:
        return None
    words = [word for word in remaining[-1].replace("_", "-").split("-") if word]
    return " ".join(word.capitalize() for word in words) or None


def _compact_current_state(state: dict) -> dict:
    result = {
        key: state.get(key)
        for key in ("url", "title", "text_excerpt", "timeline_media_present")
        if state.get(key) is not None
    }
    if result.get("text_excerpt"):
        result["text_excerpt"] = str(result["text_excerpt"])[:6000]
    return result


def _compact_grounding_candidates(candidates: list[dict]) -> list[dict]:
    keys = (
        "candidate_id", "tag", "semantic_type", "action_allowed", "text",
        "accessible_name", "aria_label", "role", "id", "name", "placeholder",
        "context_text", "upload_label", "upload_kind", "is_visible", "rect",
    )
    return [
        {
            key: candidate.get(key)
            for key in keys
            if candidate.get(key) not in (None, "", [], {})
        }
        for candidate in candidates[:25]
    ]

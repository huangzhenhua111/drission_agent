from __future__ import annotations

import json

from app.generation.capture_runner import CaptureRunner
from app.generation.planner import ActionPlan
from app.generation.planner import ActionStep


class ComplexRuntime:
    page = object()

    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.closed = False
        self.value = "5"
        self.rect_x = 1

    def state(self) -> dict:
        return {
            "url": "https://example.test/editor",
            "title": "Editor",
            "text_excerpt": "Speed Trim handle",
        }

    def snapshot(self) -> list[dict]:
        return [
            {
                "candidate_id": "target",
                "tag": "input",
                "type": "range",
                "semantic_type": "input_range",
                "action_allowed": ["input", "click"],
                "value": self.value,
                "is_visible": True,
                "rect": {"x": self.rect_x, "y": 1, "width": 100, "height": 20},
                "selector_candidates": ["css:#target"],
            }
        ]

    def set_range(self, selectors, value, target, *, selector_indexes=None):
        self.calls.append(("set_range", value, target))
        self.value = value
        return selectors[0]

    def drag(self, selectors, delta_x, delta_y, duration, target, *, selector_indexes=None):
        self.calls.append(("drag", delta_x, delta_y, duration, target))
        self.rect_x += delta_x
        return selectors[0]

    def press_key(self, key, target):
        self.calls.append(("press_key", key, target))
        return f"key:{key}"

    def wait(self, seconds: float) -> None:
        return None

    def close(self) -> None:
        self.closed = True


class ComplexGrounder:
    def ground(self, *, step: dict, candidates: list[dict], context: dict) -> dict:
        candidate = dict(candidates[0])
        return {
            "candidate_id": candidate["candidate_id"],
            "selectors": candidate["selector_candidates"],
            "selector_metadata": [],
            "reason": "test target",
            "candidate": candidate,
        }


def test_capture_runner_records_complex_editor_actions() -> None:
    runtime = ComplexRuntime()
    plan = ActionPlan(
        task="complex editor actions",
        steps=[
            ActionStep("set_range", target="Speed", value="1.5"),
            ActionStep("drag", target="Trim handle", delta_x=40, delta_y=-2, duration=0.8),
            ActionStep("press_key", target="selected clip", value="DELETE"),
        ],
    )

    captured = CaptureRunner(
        runtime=runtime,
        grounder=ComplexGrounder(),
        max_replans=0,
    ).run(plan)

    assert [action["type"] for action in captured] == ["set_range", "drag", "press_key"]
    assert captured[0]["value"] == "1.5"
    assert captured[1]["delta_x"] == 40
    assert captured[1]["duration"] == 0.8
    assert captured[2]["chosen_selector"] == "key:DELETE"
    assert runtime.closed is True


def test_after_action_screenshot_timeout_is_nonfatal_and_audited(tmp_path) -> None:
    class ScreenshotTimeoutRuntime(ComplexRuntime):
        def screenshot(self, path: str) -> None:
            raise TimeoutError("Page.captureScreenshot timed out")

    runtime = ScreenshotTimeoutRuntime()
    plan = ActionPlan(
        task="set speed while canvas is busy",
        steps=[ActionStep("set_range", target="Speed", value="1.5")],
    )

    captured = CaptureRunner(
        runtime=runtime,
        grounder=ComplexGrounder(),
        output_dir=tmp_path,
        action_delay_seconds=0.01,
        max_replans=0,
    ).run(plan)

    assert [action["type"] for action in captured] == ["set_range"]
    warning_path = tmp_path / "observation_warnings" / "after_step_00.json"
    warning = json.loads(warning_path.read_text(encoding="utf-8"))
    assert warning["operation"] == "screenshot_after_action"
    assert warning["error_type"] == "TimeoutError"
    assert "Page.captureScreenshot" in warning["error"]


def test_successful_capture_writes_raw_snapshots_only_when_debug_artifacts_enabled(tmp_path) -> None:
    plan = ActionPlan(
        task="set speed",
        steps=[ActionStep("set_range", target="Speed", value="1.5")],
    )

    CaptureRunner(
        runtime=ComplexRuntime(),
        grounder=ComplexGrounder(),
        output_dir=tmp_path / "fast",
        max_replans=0,
    ).run(plan)

    assert (tmp_path / "fast" / "dom_snapshots" / "step_00.json").exists()
    assert not (tmp_path / "fast" / "dom_snapshots" / "raw_step_00.json").exists()

    CaptureRunner(
        runtime=ComplexRuntime(),
        grounder=ComplexGrounder(),
        output_dir=tmp_path / "debug",
        max_replans=0,
        debug_artifacts=True,
    ).run(plan)

    assert (tmp_path / "debug" / "dom_snapshots" / "step_00.json").exists()
    assert (tmp_path / "debug" / "dom_snapshots" / "raw_step_00.json").exists()

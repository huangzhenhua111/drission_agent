from __future__ import annotations

import pytest

from app.generation.capture_runner import CaptureRunner
from app.generation.capture_runner import StateNotReachedError
from app.generation.capture_runner import _build_postcondition
from app.generation.capture_runner import _build_state_postcondition
from app.generation.capture_runner import _format_error_for_replan
from app.generation.capture_runner import _has_transient_text_property_overlay
from app.generation.capture_runner import _rule_based_replan_fallback
from app.generation.capture_runner import _is_text_commit_step
from app.generation.capture_runner import _mark_deferred_visual_text_origin_passed
from app.generation.capture_runner import _should_defer_visual_text_check
from app.generation.planner import ActionPlan
from app.generation.planner import ActionStep
from app.generation.visual_fallback import VisualTextCheck


class StateRuntime:
    page = object()

    def __init__(self, states: list[dict]) -> None:
        self.states = list(states)
        self.wait_calls = 0

    def state(self) -> dict:
        if len(self.states) > 1:
            return self.states.pop(0)
        return self.states[0]

    def wait(self, seconds: float) -> None:
        self.wait_calls += 1


class EditableRuntime:
    page = object()

    def __init__(self, text: str) -> None:
        self.text = text

    def snapshot(self) -> list[dict]:
        return [
            {
                "semantic_type": "contenteditable",
                "text": self.text,
                "selector_candidates": ["css:div.element.text-renderer"],
            }
        ]

    def wait(self, seconds: float) -> None:
        return None


class InputGrounder:
    def ground(self, *, step: dict, candidates: list[dict], context: dict) -> dict:
        candidate = candidates[0] if candidates else {
            "candidate_id": "title",
            "semantic_type": "contenteditable",
            "action_allowed": ["input"],
            "text": "Sample Text",
            "selector_candidates": ["css:div.element.text-renderer"],
        }
        return {
            "selectors": candidate["selector_candidates"],
            "selector_metadata": [],
            "candidate": candidate,
            "candidate_id": candidate["candidate_id"],
            "reason": "test candidate",
        }


class InputRuntime:
    page = object()

    def __init__(self) -> None:
        self.text = "Sample Text"
        self.closed = False

    def state(self) -> dict:
        return {
            "url": "https://example.test/editor",
            "title": "Editor",
            "text_excerpt": self.text,
            "html_excerpt": "",
        }

    def snapshot(self) -> list[dict]:
        return [
            {
                "candidate_id": "title",
                "semantic_type": "contenteditable",
                "action_allowed": ["input"],
                "text": self.text,
                "rect": {"x": 10, "y": 10, "width": 200, "height": 40},
                "selector_candidates": ["css:div.element.text-renderer"],
            }
        ]

    def input(
        self,
        selectors: list[str],
        value: str,
        target: str,
        *,
        selector_indexes: dict[str, int] | None = None,
    ) -> str:
        self.text = value
        return selectors[0]

    def wait(self, seconds: float) -> None:
        return None

    def screenshot(self, path: str) -> None:
        from pathlib import Path

        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)

    def close(self) -> None:
        self.closed = True


class FakeVisualTextVerifier:
    def __init__(self, check: VisualTextCheck) -> None:
        self.check = check
        self.calls: list[dict] = []

    def verify_rendered_text(self, **kwargs) -> VisualTextCheck:
        self.calls.append(kwargs)
        return self.check


class TransientOverlayRuntime:
    page = object()

    def __init__(self) -> None:
        self.overlay_open = True
        self.pressed_keys: list[str] = []

    def state(self) -> dict:
        return {
            "url": "https://example.test/editor",
            "title": "Editor",
            "text_excerpt": (
                "Text Open Sans Choose a color #000000"
                if self.overlay_open
                else "Text Open Sans 短视频测试"
            ),
        }

    def press_key(self, key: str, target: str) -> str:
        self.pressed_keys.append(key)
        if key == "ESCAPE":
            self.overlay_open = False
        return f"key:{key}"

    def wait(self, seconds: float) -> None:
        return None

    def screenshot(self, path: str) -> None:
        from pathlib import Path

        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)


class SequenceVisualTextVerifier:
    def __init__(self, checks: list[VisualTextCheck]) -> None:
        self.checks = list(checks)
        self.calls: list[dict] = []

    def verify_rendered_text(self, **kwargs) -> VisualTextCheck:
        self.calls.append(kwargs)
        if len(self.checks) > 1:
            return self.checks.pop(0)
        return self.checks[0]


class WaitRecoveryPlanner:
    def __init__(self) -> None:
        self.replan_calls = 0
        self.last_error = ""
        self.last_state: dict | None = None

    def replan(self, **kwargs) -> ActionPlan:
        self.replan_calls += 1
        self.last_error = kwargs["error"]
        self.last_state = kwargs["current_state"]
        return ActionPlan(
            task="wait for visual recovery",
            steps=[
                ActionStep(
                    "wait",
                    target="font rendering update",
                    value="0.1",
                    comment="Allow the font change to render.",
                )
            ],
        )


def test_wait_state_postcondition_uses_recent_uploaded_filename() -> None:
    postcondition = _build_state_postcondition(
        {"type": "wait", "target": "Video clip appears in timeline"},
        completed_actions=[
            {
                "type": "upload",
                "path": "/tmp/sample.mp4",
            }
        ],
    )

    assert postcondition == {"type": "timeline_media_present"}


def test_upload_does_not_require_visible_filename_immediately() -> None:
    assert (
        _build_state_postcondition(
            {"type": "upload", "path": "/tmp/sample.mp4"},
            completed_actions=[],
        )
        is None
    )


def test_contenteditable_input_requires_exact_replacement_text() -> None:
    postcondition = _build_postcondition(
        {"type": "input", "target": "Title", "value": "短视频测试"},
        {
            "semantic_type": "contenteditable",
            "selector_candidates": ["css:div.element.text-renderer"],
        },
    )

    assert postcondition == {
        "type": "editable_text_equals",
        "value": "短视频测试",
        "selectors": ["css:div.element.text-renderer"],
        "requires_visual_text_rendering": True,
    }

    runner = CaptureRunner(runtime=EditableRuntime("Sample Text短视频测试"))
    with pytest.raises(StateNotReachedError):
        runner._verify_postcondition(postcondition)

    runner = CaptureRunner(runtime=EditableRuntime("短视频测试"))
    runner._verify_postcondition(postcondition)


def test_cjk_contenteditable_input_records_readable_visual_text(tmp_path) -> None:
    verifier = FakeVisualTextVerifier(
        VisualTextCheck(
            readable=True,
            exact_text_visible=True,
            observed_text="短视频测试",
            issue="none",
            reason="expected text is visibly rendered",
        )
    )
    plan = ActionPlan(
        task="replace title text",
        steps=[ActionStep("input", target="Title", value="短视频测试")],
    )

    captured = CaptureRunner(
        runtime=InputRuntime(),
        grounder=InputGrounder(),
        visual_text_verifier=verifier,
        output_dir=tmp_path,
        max_replans=0,
    ).run(plan)

    assert captured[0]["postcondition_passed"] is True
    assert captured[0]["rendered_text_readable"] is True
    assert captured[0]["visual_text_check"]["issue"] == "none"
    assert verifier.calls[0]["expected_text"] == "短视频测试"


def test_cjk_contenteditable_input_fails_when_visual_text_is_unreadable(tmp_path) -> None:
    verifier = FakeVisualTextVerifier(
        VisualTextCheck(
            readable=False,
            exact_text_visible=False,
            observed_text="□□□□□",
            issue="tofu_boxes",
            reason="expected Chinese text is shown as missing-glyph boxes",
        )
    )
    plan = ActionPlan(
        task="replace title text",
        steps=[ActionStep("input", target="Title", value="短视频测试")],
    )

    with pytest.raises(StateNotReachedError, match="not visibly readable"):
        CaptureRunner(
            runtime=InputRuntime(),
            grounder=InputGrounder(),
            visual_text_verifier=verifier,
            output_dir=tmp_path,
            max_replans=0,
        ).run(plan)

    assert (tmp_path / "visual_text_checks" / "step_00.json").exists()


def test_replan_error_includes_visual_text_check_details() -> None:
    error = StateNotReachedError(
        "Postcondition failed: expected editable text matched DOM but was not visibly readable",
        expected={
            "type": "editable_text_equals",
            "value": "短视频测试",
            "visual_text_check": {
                "readable": False,
                "exact_text_visible": False,
                "issue": "tofu_boxes",
                "reason": "Chinese text is shown as missing-glyph boxes.",
            },
        },
    )

    formatted = _format_error_for_replan(error)

    assert "visual_text_check=" in formatted
    assert '"issue": "tofu_boxes"' in formatted
    assert "changing the selected font/style" in formatted


def test_visual_text_recovery_can_pass_after_replan_wait(tmp_path) -> None:
    verifier = SequenceVisualTextVerifier(
        [
            VisualTextCheck(
                readable=False,
                exact_text_visible=False,
                observed_text="□□□□□",
                issue="tofu_boxes",
                reason="expected Chinese text is shown as missing-glyph boxes",
            ),
            VisualTextCheck(
                readable=True,
                exact_text_visible=True,
                observed_text="短视频测试",
                issue="none",
                reason="font change rendered the expected text",
            ),
        ]
    )
    planner = WaitRecoveryPlanner()
    plan = ActionPlan(
        task="replace title text",
        steps=[ActionStep("input", target="Title", value="短视频测试")],
    )

    captured = CaptureRunner(
        runtime=InputRuntime(),
        grounder=InputGrounder(),
        planner=planner,
        visual_text_verifier=verifier,
        output_dir=tmp_path,
        max_replans=1,
    ).run(plan)

    assert planner.replan_calls == 1
    assert "visual_text_check=" in planner.last_error
    assert (planner.last_state or {}).get("visual_text_check", {}).get("issue") == "tofu_boxes"
    assert [action["type"] for action in captured] == ["wait"]
    assert captured[0]["visual_text_recovery_passed"] is True
    assert captured[0]["rendered_text_readable"] is True
    assert captured[0]["visual_text_check"]["issue"] == "none"
    assert len(verifier.calls) == 2


def test_visual_text_recovery_failure_after_replan_does_not_capture_success(tmp_path) -> None:
    verifier = SequenceVisualTextVerifier(
        [
            VisualTextCheck(
                readable=False,
                exact_text_visible=False,
                observed_text="□□□□□",
                issue="tofu_boxes",
                reason="expected Chinese text is shown as missing-glyph boxes",
            ),
            VisualTextCheck(
                readable=False,
                exact_text_visible=False,
                observed_text="□□□□□",
                issue="tofu_boxes",
                reason="font change still rendered missing-glyph boxes",
            ),
        ]
    )
    planner = WaitRecoveryPlanner()
    plan = ActionPlan(
        task="replace title text",
        steps=[ActionStep("input", target="Title", value="短视频测试")],
    )

    with pytest.raises(StateNotReachedError, match="recovery did not produce readable"):
        CaptureRunner(
            runtime=InputRuntime(),
            grounder=InputGrounder(),
            planner=planner,
            visual_text_verifier=verifier,
            output_dir=tmp_path,
            max_replans=1,
        ).run(plan)

    assert planner.replan_calls == 1
    # A failed recovery receives one final workflow-boundary verification;
    # success above returns immediately and therefore does not duplicate it.
    assert len(verifier.calls) == 3


def test_upload_filename_assertion_expires_after_navigation() -> None:
    postcondition = _build_state_postcondition(
        {"type": "wait", "target": "homepage", "comment": "Wait for homepage to load"},
        completed_actions=[
            {"type": "upload", "path": "/tmp/sample.mp4"},
            {"type": "goto", "url": "https://example.test/"},
        ],
    )

    assert postcondition == {
        "type": "visible_text_contains",
        "values": ["homepage"],
    }
    assert "sample.mp4" not in postcondition["values"]


def test_generic_wait_after_failed_upload_does_not_reassert_filename() -> None:
    postcondition = _build_state_postcondition(
        {"type": "wait", "value": "5", "comment": "等待页面可能的后台加载"},
        completed_actions=[{"type": "upload", "path": "/tmp/sample.mp4"}],
    )

    assert postcondition is None


def test_export_dialog_wait_uses_cross_language_structural_markers() -> None:
    postcondition = _build_state_postcondition(
        {
            "type": "wait",
            "target": "导出设置界面",
            "comment": "等待导出流程打开",
        },
        completed_actions=[{"type": "click", "target": "Export"}],
    )

    assert postcondition == {
        "type": "visible_text_all",
        "values": ["export", "format"],
    }


def test_upload_confirmation_comment_reasserts_filename() -> None:
    postcondition = _build_state_postcondition(
        {"type": "wait", "value": "5", "comment": "等待视频加载并出现在时间线"},
        completed_actions=[{"type": "upload", "path": "/tmp/sample.mp4"}],
    )

    assert postcondition == {"type": "timeline_media_present"}


def test_wait_with_explicit_filename_requires_it_without_adjacent_upload() -> None:
    postcondition = _build_state_postcondition(
        {"type": "wait", "target": "sample.mp4 出现在时间线", "value": "10"},
        completed_actions=[{"type": "click", "target": "Create Project"}],
    )

    assert postcondition == {
        "type": "visible_text_contains",
        "values": ["sample.mp4"],
    }


def test_click_on_unusable_page_is_rejected() -> None:
    runner = CaptureRunner(runtime=StateRuntime([{"text_excerpt": "unused"}]))

    with pytest.raises(StateNotReachedError):
        runner._verify_navigation_state(
            {"type": "click", "target": "Online video editor"},
            {
                "url": "https://example.test/missing",
                "title": "404 - Page not found",
                "text_excerpt": "The page was not found on this server.",
            },
        )


def test_create_project_click_requires_real_editor_transition() -> None:
    runtime = StateRuntime([{"url": "https://example.test/editor", "text_excerpt": "Create Project"}])
    runner = CaptureRunner(runtime=runtime)

    with pytest.raises(StateNotReachedError) as exc_info:
        runner._verify_click_state_transition(
            {"type": "click", "target": "Create Project button"},
            before_state={"url": "https://example.test/editor"},
            current_state={"url": "https://example.test/editor", "text_excerpt": "Video menu"},
        )

    assert exc_info.value.expected["type"] == "project_created"


def test_create_project_click_accepts_project_url_transition() -> None:
    runner = CaptureRunner(runtime=StateRuntime([{"text_excerpt": "unused"}]))
    state = runner._verify_click_state_transition(
        {"type": "click", "target": "Create Project"},
        before_state={"url": "https://example.test/editor"},
        current_state={
            "url": "https://example.test/projects/123",
            "text_excerpt": "New project Export Add files Media Text Canvas",
        },
    )

    assert state["url"].endswith("/projects/123")


def test_create_project_click_rejects_unrelated_url_transition() -> None:
    runtime = StateRuntime(
        [{"url": "https://example.test/add-image-to-video", "text_excerpt": "Add Image to Video"}]
    )
    runner = CaptureRunner(runtime=runtime)

    with pytest.raises(StateNotReachedError):
        runner._verify_click_state_transition(
            {"type": "click", "target": "Create Project"},
            before_state={"url": "https://example.test/video-editor"},
            current_state={
                "url": "https://example.test/add-image-to-video",
                "text_excerpt": "Add Image to Video",
            },
        )


def test_verify_state_postcondition_polls_until_text_is_visible() -> None:
    runtime = StateRuntime(
        [
            {"text_excerpt": "Loading"},
            {"text_excerpt": "Trim Reset sample.mp4 Save"},
        ]
    )
    runner = CaptureRunner(runtime=runtime)

    state = runner._verify_state_postcondition(
        {"type": "visible_text_contains", "values": ["sample.mp4"]},
        current_state=runtime.state(),
    )

    assert "sample.mp4" in state["text_excerpt"]
    assert runtime.wait_calls == 1


def test_verify_state_postcondition_raises_state_not_reached() -> None:
    runtime = StateRuntime([{"text_excerpt": "Still loading"}])
    runner = CaptureRunner(runtime=runtime)

    with pytest.raises(StateNotReachedError) as exc_info:
        runner._verify_state_postcondition(
            {"type": "visible_text_contains", "values": ["sample.mp4"]},
            current_state=runtime.state(),
        )

    assert exc_info.value.reason == "state_not_reached"
    assert exc_info.value.expected["values"] == ["sample.mp4"]


def test_verify_state_postcondition_accepts_all_export_dialog_markers() -> None:
    runtime = StateRuntime(
        [{"text_excerpt": "Export Format MP4 480p 720p 1080p"}]
    )
    runner = CaptureRunner(runtime=runtime)

    state = runner._verify_state_postcondition(
        {"type": "visible_text_all", "values": ["export", "format"]},
        current_state=runtime.state(),
    )

    assert "Format" in state["text_excerpt"]


def test_verify_navigation_state_rejects_404_page() -> None:
    runner = CaptureRunner(runtime=StateRuntime([{"text_excerpt": "unused"}]))

    with pytest.raises(StateNotReachedError) as exc_info:
        runner._verify_navigation_state(
            {"type": "goto", "url": "https://example.test/missing"},
            {
                "url": "https://example.test/missing",
                "title": "404 - Page not found",
                "text_excerpt": "The page was not found on this server.",
            },
        )

    assert exc_info.value.reason == "state_not_reached"
    assert exc_info.value.expected["type"] == "navigation_reachable"


def test_rule_based_replan_fallback_recovers_unreachable_feature_route() -> None:
    error = StateNotReachedError(
        "Navigation did not reach a usable page",
        expected={"type": "navigation_reachable"},
    )

    plan = _rule_based_replan_fallback(
        task="打开视频编辑器，上传 /tmp/sample.mp4，然后等待进入编辑器",
        failed_step={"type": "goto", "url": "https://123apps.com/video-editor/"},
        current_state={"url": "https://123apps.com/video-editor/"},
        error=error,
    )

    assert plan is not None
    assert [step.type for step in plan.steps] == ["goto", "click", "upload", "wait"]
    assert plan.steps[0].url == "https://123apps.com/"
    assert plan.steps[1].target == "Video Editor"
    assert plan.steps[2].target == "file upload area"
    assert plan.steps[2].path == "/tmp/sample.mp4"
def test_wait_after_double_click_requires_timeline_editor_evidence() -> None:
    postcondition = _build_state_postcondition(
        {"type": "wait", "target": "Timeline", "value": "2"},
        completed_actions=[{"type": "double_click", "target": "sample.mp4"}],
    )

    assert postcondition == {"type": "timeline_media_present"}


def test_cjk_visual_check_is_deferred_until_requested_text_styling_commits() -> None:
    postcondition = {
        "type": "editable_text_equals",
        "value": "短视频测试",
        "requires_visual_text_rendering": True,
    }

    assert _should_defer_visual_text_check(
        postcondition,
        remaining_steps=[
            ActionStep("click", target="Text color button"),
            ActionStep("click", target="Black color swatch"),
            ActionStep("click", target="Canvas outside text box", comment="Commit text"),
        ],
    )
    assert _is_text_commit_step(
        {"type": "click", "target": "Canvas outside text box", "comment": "Commit text"}
    )


def test_color_picker_is_treated_as_transient_text_property_overlay() -> None:
    assert _has_transient_text_property_overlay(
        {"text_excerpt": "Text Open Sans 32 Choose a color #000000 100%"}
    )
    assert not _has_transient_text_property_overlay(
        {"text_excerpt": "Export settings Resolution 1080p Continue"}
    )


def test_deferred_visual_check_dismisses_color_picker_before_screenshot(tmp_path) -> None:
    runtime = TransientOverlayRuntime()
    verifier = FakeVisualTextVerifier(
        VisualTextCheck(
            readable=True,
            exact_text_visible=True,
            observed_text="短视频测试",
            issue="none",
            reason="Visible after dismissing the property picker.",
        )
    )
    runner = CaptureRunner(
        runtime=runtime,
        visual_text_verifier=verifier,
        output_dir=tmp_path,
    )

    result = runner._try_verify_visual_text_recovery(
        step_index=12,
        step={"type": "wait", "comment": "Verify text is readable"},
        postcondition={
            "type": "editable_text_equals",
            "value": "短视频测试",
            "requires_visual_text_rendering": True,
            "_defer_until_commit": True,
        },
        current_state=runtime.state(),
    )

    assert runtime.pressed_keys == ["ESCAPE"]
    assert verifier.calls[0]["context"]["text_excerpt"] == "Text Open Sans 短视频测试"
    assert result is not None
    assert result["readable"] is True


def test_deferred_visual_check_marks_originating_input_readable() -> None:
    actions = [
        {
            "step_index": 4,
            "type": "input",
            "value": "短视频测试",
            "rendered_text_readable": False,
        }
    ]
    pending = {
        "type": "editable_text_equals",
        "value": "短视频测试",
        "_defer_until_commit": True,
        "_origin_step_index": 4,
    }
    check = {"readable": True, "exact_text_visible": True, "issue": "none"}

    _mark_deferred_visual_text_origin_passed(actions, pending, check)

    assert actions[0]["rendered_text_readable"] is True
    assert actions[0]["visual_text_check_deferred"] is True
    assert actions[0]["visual_text_check"] == check

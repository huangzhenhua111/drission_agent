from __future__ import annotations

from app.debug.failure_context import classify_failure
from app.debug.failure_context import context_from_capture_failure
from app.debug.failure_context import context_from_script_run
from app.debug.failure_context import normalize_failure_context
from app.generation.capture_runner import CaptureRunner
from app.resilience.executor import ReplanRequired


class FailureContextRuntime:
    def state(self) -> dict:
        return {"url": "https://example.test/form", "title": "Form"}

    def screenshot(self, path: str) -> None:
        with open(path, "wb") as handle:
            handle.write(b"not-a-real-png")


def category(context: dict) -> str:
    return classify_failure(context)["category"]


def test_capture_failure_context_is_normalized_and_classified_selector_miss() -> None:
    raw = {
        "step": {"type": "click", "target": "Submit", "fallback_selectors": ["#submit"]},
        "error_type": "ReplanRequired",
        "error": "click target 'Submit' failed after retry",
        "replan_reason": "selector_miss",
        "state": {"url": "https://example.test/form"},
        "candidate_count": 0,
    }

    context = context_from_capture_failure(raw)
    classification = classify_failure(context)

    assert context["schema_version"] == 1
    assert context["selectors"] == ["#submit"]
    assert classification["category"] == "selector_miss"
    assert classification["confidence"] >= 0.9
    assert classification["llm_escalation_allowed"] is False


def test_capture_runner_writes_normalized_failure_context_and_classification(tmp_path) -> None:
    runner = CaptureRunner(runtime=FailureContextRuntime(), output_dir=tmp_path)
    error = ReplanRequired("No selectors available for click target 'Submit'.", reason="selector_miss")

    runner._write_failure_context(
        step_index=2,
        step={"type": "click", "target": "Submit", "fallback_selectors": []},
        raw_candidates=[],
        candidates=[],
        error=error,
    )

    assert (tmp_path / "failures" / "step_02.json").exists()
    context_path = tmp_path / "failures" / "failure_context_step_02.json"
    classification_path = tmp_path / "failures" / "classification_step_02.json"
    assert context_path.exists()
    assert classification_path.exists()
    assert '"schema_version": 1' in context_path.read_text(encoding="utf-8")
    assert '"category": "selector_miss"' in classification_path.read_text(encoding="utf-8")


def test_script_run_context_uses_current_action_and_postcondition() -> None:
    script_run = {
        "script_path": "/tmp/run/generated_script.py",
        "returncode": 1,
        "stderr": "RuntimeError: State postcondition failed: no media clip was detected in the timeline",
        "metrics_path": "/tmp/run/generated_script_metrics.json",
        "metrics": {
            "current_action": {
                "type": "wait",
                "target": "uploaded file appears in editor timeline",
                "state_postcondition": {"type": "timeline_media_present"},
            },
            "error": "RuntimeError: State postcondition failed: no media clip was detected in the timeline",
            "final_state": {"url": "https://online-video-cutter.com/projects/abc"},
            "llm_calls": {"total": 0},
        },
    }

    context = context_from_script_run(script_run)

    assert context["step"]["target"] == "uploaded file appears in editor timeline"
    assert context["postcondition"] == {"type": "timeline_media_present"}
    assert category(context) == "postcondition_failed"


def test_classifier_distinguishes_security_from_selector_failure() -> None:
    context = normalize_failure_context(
        step={"type": "click", "target": "Continue", "fallback_selectors": ["text=Continue"]},
        error="Element lookup failed for Continue",
        state={"text_excerpt": "Cloudflare Verify you are human before continuing"},
        selectors=["text=Continue"],
    )

    assert category(context) == "security_challenge"


def test_classifier_distinguishes_auth_from_public_sign_in_copy() -> None:
    auth_context = normalize_failure_context(
        error_type="AuthenticationRequired",
        error="Login required",
        state={"url": "https://example.test/login", "title": "Login"},
    )
    public_context = normalize_failure_context(
        error="Element lookup failed for Pricing",
        state={"url": "https://example.test/", "text_excerpt": "Pricing Help Sign In"},
        step={"type": "click", "target": "Pricing"},
        selectors=["text=Pricing"],
    )

    assert category(auth_context) == "auth_required"
    assert category(public_context) == "selector_miss"


def test_classifier_selector_not_unique_beats_selector_miss() -> None:
    context = normalize_failure_context(
        error="Element lookup failed for Save: strict mode violation, multiple elements matched",
        step={"type": "click", "target": "Save", "fallback_selectors": ["text=Save"]},
        selectors=["text=Save"],
    )

    assert category(context) == "selector_not_unique"


def test_classifier_ambiguous_target_from_duplicate_candidates() -> None:
    context = normalize_failure_context(
        error="target ambiguous",
        step={"type": "click", "target": "Video"},
        selectors=["text=Video"],
        top_candidates=[
            {"text": "Video", "is_visible": True},
            {"text": "Video", "is_visible": True},
            {"accessible_name": "Video", "is_visible": True},
        ],
    )

    assert category(context) == "ambiguous_target"


def test_classifier_visual_mapping_untrusted() -> None:
    context = normalize_failure_context(
        error="Visual fallback coordinates are outside the screenshot: (2000, 50)",
        step={"type": "click", "target": "Open Sans"},
    )

    assert category(context) == "visual_mapping_untrusted"


def test_classifier_distinguishes_overlay_from_unreadable_text() -> None:
    overlay = normalize_failure_context(
        error="Postcondition failed: expected editable text matched DOM but was not visibly readable",
        postcondition={
            "type": "editable_text_equals",
            "requires_visual_text_rendering": True,
            "visual_text_check": {"readable": False, "issue": "obscured_by_overlay"},
        },
    )
    tofu = normalize_failure_context(
        error="Postcondition failed: expected editable text matched DOM but was not visibly readable",
        postcondition={
            "type": "editable_text_equals",
            "requires_visual_text_rendering": True,
            "visual_text_check": {"readable": False, "issue": "tofu_boxes"},
        },
    )

    assert category(overlay) == "wrong_target"
    assert category(tofu) == "task_unsatisfiable"


def test_classifier_distinguishes_loading_delay_from_unchanged_state() -> None:
    loading = normalize_failure_context(
        error="State postcondition failed: upload did not appear",
        state={"text_excerpt": "Uploading... please wait"},
        before_state={"url": "https://example.test/projects/1"},
        after_state={"url": "https://example.test/projects/1"},
    )
    unchanged = normalize_failure_context(
        error="State postcondition failed: no tab changed",
        before_state={"url": "https://example.test/a", "title": "A"},
        after_state={"url": "https://example.test/a", "title": "A"},
    )

    assert category(loading) == "network_or_load_delay"
    assert category(unchanged) == "postcondition_failed"


def test_classifier_wrong_target_when_state_changed_but_postcondition_failed() -> None:
    context = normalize_failure_context(
        error="RuntimeError: State postcondition failed: expected My Library tab selected",
        step={"type": "click", "target": "My Library"},
        postcondition={"type": "tab_selected", "label": "My Library"},
        before_state={"url": "https://example.test/editor", "title": "Editor"},
        after_state={"url": "https://example.test/library", "title": "Stock Library"},
    )

    classification = classify_failure(context)

    assert classification["category"] == "wrong_target"
    assert classification["recommended_strategy"] == "requery_target_and_replay_postcondition"


def test_classifier_state_unchanged_without_postcondition() -> None:
    context = normalize_failure_context(
        error="Action completed but page state did not change",
        step={"type": "click", "target": "Create Project"},
        selectors=["css:.create-project"],
        before_state={"url": "https://example.test/start", "title": "Start"},
        after_state={"url": "https://example.test/start", "title": "Start"},
    )

    assert category(context) == "state_unchanged"


def test_script_run_context_falls_back_to_next_action_from_completed_metrics() -> None:
    actions = [
        {"type": "goto", "url": "https://example.test/form"},
        {"type": "click", "target": "Submit", "fallback_selectors": ["#submit"]},
    ]
    script_run = {
        "script_path": "/tmp/run/generated_script.py",
        "returncode": 1,
        "stderr": "RuntimeError: Element lookup failed for Submit: no match",
        "metrics": {
            "actions": [{"index": 1, "type": "goto"}],
            "error": "RuntimeError: Element lookup failed for Submit: no match",
            "final_state": {"url": "https://example.test/form"},
        },
    }

    context = context_from_script_run(script_run, actions=actions)

    assert context["step"]["target"] == "Submit"
    assert context["selectors"] == ["#submit"]
    assert category(context) == "selector_miss"


def test_classifier_budget_exhausted_and_low_confidence_runtime() -> None:
    budget = classify_failure(normalize_failure_context(error_type="TimeoutExpired", error="timed out after 120s"))
    runtime = classify_failure(normalize_failure_context(error_type="RuntimeError", error="Unexpected widget failure"))

    assert budget["category"] == "budget_exhausted"
    assert budget["recoverable"] is False
    assert runtime["category"] == "runtime_exception"
    assert runtime["llm_escalation_allowed"] is True

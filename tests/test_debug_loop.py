from __future__ import annotations

import json

import pytest

from app.debug.fixer import ScriptFixer
from app.debug.loop import DebugLoop


def write_failing_script(path, metrics: dict, error: str) -> None:
    path.write_text(
        "\n".join(
            [
                "from pathlib import Path",
                "import json",
                f"METRICS = {metrics!r}",
                "Path('generated_script_metrics.json').write_text(",
                "    json.dumps(METRICS, ensure_ascii=False, indent=2),",
                "    encoding='utf-8',",
                ")",
                f"raise RuntimeError({error!r})",
            ]
        ),
        encoding="utf-8",
    )


def test_debug_loop_collects_classified_attempt_and_fix_request(tmp_path) -> None:
    script_path = tmp_path / "generated_script.py"
    write_failing_script(
        script_path,
        {
            "success": False,
            "current_action": {
                "type": "click",
                "target": "Submit",
                "fallback_selectors": ["#missing"],
            },
            "error": "RuntimeError: Element lookup failed for Submit: no match",
            "final_state": {"url": "https://example.test/form"},
            "llm_calls": {"total": 0},
        },
        "RuntimeError: Element lookup failed for Submit: no match",
    )
    artifacts = tmp_path / "debug"

    attempt = DebugLoop(output_dir=artifacts).collect_attempt(str(script_path))

    assert attempt["success"] is False
    assert attempt["classification"]["category"] == "selector_miss"
    assert attempt["fix_request"]["category"] == "selector_miss"
    assert attempt["fix_request"]["requires_llm"] is False
    assert attempt["fix_request"]["patch_constraints"]["preserve_postconditions"] is True
    assert (artifacts / "debug_attempt_01.json").exists()
    assert (artifacts / "failure_context_01.json").exists()
    assert (artifacts / "classification_01.json").exists()
    assert (artifacts / "fix_request_01.json").exists()


def test_debug_loop_run_stops_before_patch_phase_with_classification(tmp_path) -> None:
    script_path = tmp_path / "generated_script.py"
    write_failing_script(
        script_path,
        {
            "success": False,
            "current_action": {"type": "wait", "state_postcondition": {"type": "timeline_media_present"}},
            "error": "RuntimeError: State postcondition failed: no media clip was detected in the timeline",
            "final_state": {"url": "https://example.test/projects/1"},
            "llm_calls": {"total": 0},
        },
        "RuntimeError: State postcondition failed: no media clip was detected in the timeline",
    )

    with pytest.raises(NotImplementedError, match="postcondition_failed"):
        DebugLoop(output_dir=tmp_path / "debug").run(str(script_path))


def test_script_fixer_builds_request_from_classification_without_llm() -> None:
    failure_context = {
        "schema_version": 1,
        "step": {"type": "click", "target": "Open Sans", "visual_position": {"x": 10, "y": 10}},
        "error_type": "ValueError",
        "error": "Visual fallback did not report the visible label for named target 'Open Sans'",
        "state": {},
        "selectors": [],
        "postcondition": {},
        "screenshot": "/tmp/failure.png",
        "retry_count": 0,
        "before_state": {},
        "after_state": {},
        "timings": {},
        "llm_calls": {"total": 0},
        "artifact_paths": {},
    }

    request = ScriptFixer().build_fix_request(failure_context)

    assert request["category"] == "visual_mapping_untrusted"
    assert request["requires_llm"] is False
    assert request["allowed_patch_scope"] == [
        "visual_grounding_request",
        "selector_backfill_evidence",
    ]


def test_debug_loop_success_attempt_writes_no_failure_contract(tmp_path) -> None:
    script_path = tmp_path / "generated_script.py"
    script_path.write_text(
        "from pathlib import Path\n"
        "import json\n"
        "Path('generated_script_metrics.json').write_text(json.dumps({'success': True}), encoding='utf-8')\n",
        encoding="utf-8",
    )
    artifacts = tmp_path / "debug"

    attempt = DebugLoop(output_dir=artifacts).collect_attempt(str(script_path))

    assert attempt["success"] is True
    assert attempt["failure_context"] is None
    assert attempt["classification"] is None
    assert (artifacts / "debug_attempt_01.json").exists()
    assert not (artifacts / "classification_01.json").exists()

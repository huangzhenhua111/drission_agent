from __future__ import annotations

from app.debug.runner import DebugRunner


def test_debug_runner_includes_generated_script_metrics(tmp_path) -> None:
    script_path = tmp_path / "generated_script.py"
    script_path.write_text(
        "\n".join(
            [
                "from pathlib import Path",
                "import json",
                "Path('generated_script_metrics.json').write_text(",
                "    json.dumps({'success': True, 'llm_calls': {'total': 0}}),",
                "    encoding='utf-8',",
                ")",
            ]
        ),
        encoding="utf-8",
    )

    result = DebugRunner().run_script(str(script_path))

    assert result["success"] is True
    assert result["metrics_path"] == str(tmp_path / "generated_script_metrics.json")
    assert result["metrics"] == {"success": True, "llm_calls": {"total": 0}}


def test_debug_runner_tolerates_invalid_generated_script_metrics(tmp_path) -> None:
    script_path = tmp_path / "generated_script.py"
    script_path.write_text(
        "from pathlib import Path\nPath('generated_script_metrics.json').write_text('{bad', encoding='utf-8')\n",
        encoding="utf-8",
    )

    result = DebugRunner().run_script(str(script_path))

    assert result["success"] is True
    assert result["metrics_path"] == str(tmp_path / "generated_script_metrics.json")
    assert result["metrics"] == {
        "error": "generated_script_metrics.json is not valid JSON"
    }


def test_debug_runner_classifies_failed_script_run(tmp_path) -> None:
    script_path = tmp_path / "generated_script.py"
    script_path.write_text(
        "\n".join(
            [
                "from pathlib import Path",
                "import json",
                "Path('generated_script_metrics.json').write_text(",
                "    json.dumps({",
                "      'success': False,",
                "      'current_action': {'type': 'click', 'target': 'Submit', 'fallback_selectors': ['#missing']},",
                "      'error': 'RuntimeError: Element lookup failed for Submit: no match',",
                "      'final_state': {'url': 'https://example.test/form'},",
                "      'llm_calls': {'total': 0},",
                "    }),",
                "    encoding='utf-8',",
                ")",
                "raise RuntimeError('Element lookup failed for Submit: no match')",
            ]
        ),
        encoding="utf-8",
    )

    result = DebugRunner().run_script(str(script_path))

    assert result["success"] is False
    assert result["failure_context"]["step"]["target"] == "Submit"
    assert result["classification"]["category"] == "selector_miss"
    assert result["classification"]["llm_escalation_allowed"] is False

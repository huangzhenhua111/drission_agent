from __future__ import annotations

from app.validation.performance_report import build_performance_report


def test_performance_report_compares_capture_and_zero_llm_replay() -> None:
    captured_actions = [
        {
            "type": "goto",
            "after_url": "https://example.test/editor",
            "after_title": "Editor",
            "after_text_excerpt": "Create Project",
        },
        {
            "type": "input",
            "value": "短视频测试",
            "fallback_selectors": ["css:.title"],
            "after_url": "https://example.test/projects/123",
            "after_title": "Editor",
            "after_text_excerpt": "Canvas 短视频测试",
        },
        {
            "type": "click",
            "chosen_selector": "visual:10,20",
            "visual_position": {"x": 10, "y": 20},
            "fallback_selectors": [],
            "state_postcondition": {"type": "timeline_media_present"},
            "timings": {
                "snapshot_before_seconds": 0.2,
                "visual_model_seconds": 1.5,
                "click_execution_seconds": 0.1,
            },
            "after_url": "https://example.test/projects/123",
            "after_title": "Editor",
            "after_text_excerpt": "Canvas 短视频测试",
        },
    ]
    generation_trace = [
        {"elapsed_seconds": 1.0, "captured_action": captured_actions[0]},
        {"elapsed_seconds": 2.0, "captured_action": captured_actions[1]},
        {"elapsed_seconds": 3.0, "captured_action": captured_actions[2]},
    ]
    script_run = {
        "success": True,
        "returncode": 0,
        "metrics_path": "/tmp/generated_script_metrics.json",
        "metrics": {
            "llm_calls": {"total": 0},
            "total_seconds": 4.0,
            "pure_execution_seconds": 4.0,
            "selector_lookup_count": 2,
            "selector_lookup_seconds": 0.3,
            "visual_coordinate_clicks": 1,
            "action_delay_seconds": 0.0,
            "postconditions": [
                {
                    "action_type": "click",
                    "type": "timeline_media_present",
                    "passed": True,
                }
            ],
            "actions": [{"type": "goto"}, {"type": "input"}, {"type": "click"}],
            "final_state": {
                "url": "https://example.test/projects/999",
                "title": "Editor",
                "text_excerpt": "Canvas 短视频测试",
            },
        },
    }

    report = build_performance_report(
        task="输入标题短视频测试",
        captured_actions=captured_actions,
        generation_trace=generation_trace,
        capture_assertions={"passed": True, "issues": []},
        success_assertions=[{"type": "url_contains", "value": "/projects/"}],
        script_run=script_run,
        snapshot_stats={"file_count": 2, "total_bytes": 1000},
    )

    assert report["capture"]["total_seconds"] == 6.0
    assert report["capture"]["timings"]["visual_model_seconds"] == 1.5
    assert report["capture"]["llm_calls"]["total"] == 2
    assert report["capture"]["llm_seconds"] == 1.5
    assert report["capture"]["snapshot_stats"]["file_count"] == 2
    assert report["capture"]["visual_coordinate_clicks"] == 1
    assert report["replay"]["available"] is True
    assert report["replay"]["llm_calls"]["total"] == 0
    assert report["comparison"]["passed"] is True
    assert report["comparison"]["replay_zero_llm"] is True
    assert {
        "type": "url_contains",
        "value": "/projects/",
        "passed": True,
        "reason": "replay final URL does not contain '/projects/'",
    } in report["comparison"]["postconditions"]
    assert {
        "type": "state_postcondition",
        "value": "timeline_media_present",
        "passed": True,
        "reason": "replay did not record passing state postcondition 'timeline_media_present'",
    } in report["comparison"]["postconditions"]
    assert {
        "type": "visible_text_contains",
        "value": "短视频测试",
        "passed": True,
        "reason": "replay final state does not contain expected text '短视频测试'",
    } in report["comparison"]["postconditions"]


def test_performance_report_flags_replay_postcondition_mismatch() -> None:
    report = build_performance_report(
        task="输入标题短视频测试",
        captured_actions=[
            {
                "type": "input",
                "value": "短视频测试",
                "after_url": "https://example.test/projects/123",
                "after_title": "Editor",
                "after_text_excerpt": "Canvas 短视频测试",
                "state_postcondition": {"type": "timeline_media_present"},
            }
        ],
        generation_trace=[{"elapsed_seconds": 1.0}],
        capture_assertions={"passed": True, "issues": []},
        success_assertions=[{"type": "title_contains", "value": "Submitted"}],
        script_run={
            "success": True,
            "returncode": 0,
            "metrics": {
                "llm_calls": {"total": 1},
                "actions": [],
                "final_state": {
                    "url": "https://example.test/home",
                    "title": "Editor",
                    "text_excerpt": "Canvas",
                },
            },
        },
    )

    assert report["comparison"]["passed"] is False
    assert "generated replay used LLM calls" in report["comparison"]["issues"]
    assert "replay final URL does not contain /projects/" in report["comparison"]["issues"]
    assert "replay final title does not contain 'Submitted'" in report["comparison"]["issues"]
    assert (
        "replay final state does not contain expected text '短视频测试'"
        in report["comparison"]["issues"]
    )
    assert (
        "replay did not record passing state postcondition 'timeline_media_present'"
        in report["comparison"]["issues"]
    )

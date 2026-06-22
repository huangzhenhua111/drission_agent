from __future__ import annotations

from collections.abc import Iterable
from typing import Any


def build_performance_report(
    *,
    task: str,
    captured_actions: list[dict],
    generation_trace: list[dict] | None = None,
    capture_assertions: dict | None = None,
    success_assertions: list[dict] | None = None,
    script_run: dict | None = None,
    snapshot_stats: dict | None = None,
) -> dict:
    capture = _capture_summary(captured_actions, generation_trace or [], snapshot_stats or {})
    replay = _replay_summary(script_run or {})
    comparison = _compare_capture_and_replay(
        task=task,
        captured_actions=captured_actions,
        capture_assertions=capture_assertions or {},
        success_assertions=success_assertions or [],
        replay=replay,
    )
    return {
        "schema_version": 1,
        "task": task,
        "capture": capture,
        "replay": replay,
        "comparison": comparison,
    }


def _capture_summary(
    captured_actions: list[dict],
    generation_trace: list[dict],
    snapshot_stats: dict,
) -> dict:
    trace_elapsed = [
        _float_or_zero(entry.get("elapsed_seconds"))
        for entry in generation_trace
        if isinstance(entry, dict)
    ]
    action_elapsed = [
        _float_or_zero(action.get("elapsed_seconds"))
        for action in captured_actions
        if isinstance(action, dict) and action.get("elapsed_seconds") is not None
    ]
    elapsed_values = trace_elapsed or action_elapsed
    visual_actions = [
        action
        for action in captured_actions
        if str(action.get("chosen_selector") or "").startswith("visual:")
        or (
            action.get("visual_position") is not None
            and not (action.get("fallback_selectors") or [])
        )
    ]
    timing_totals = _sum_timing_buckets(
        _iter_action_timing_dicts(captured_actions, generation_trace)
    )
    llm_calls = {
        "planner": 1,
        "replanner": _count_trace_replans(generation_trace),
        "visual_grounding": sum(
            1 for action in captured_actions if action.get("visual_position") is not None
        ),
        "visual_verification": sum(
            1 for action in captured_actions if action.get("visual_text_check")
        ),
    }
    llm_calls["total"] = sum(llm_calls.values())
    wait_action_seconds = sum(
        _float_or_zero(action.get("seconds"))
        for action in captured_actions
        if action.get("type") == "wait"
    )
    return {
        "action_count": len(captured_actions),
        "total_seconds": round(sum(elapsed_values), 4),
        "pure_execution_seconds": round(sum(elapsed_values), 4),
        "llm_calls": llm_calls,
        "llm_seconds": round(timing_totals.get("visual_model_seconds", 0.0), 4),
        "wait_action_seconds": round(wait_action_seconds, 4),
        "selector_actions": sum(
            1 for action in captured_actions if action.get("fallback_selectors")
        ),
        "visual_coordinate_clicks": len(visual_actions),
        "timings": timing_totals,
        "snapshot_stats": snapshot_stats,
        "final_state": _final_state_from_capture(captured_actions),
    }


def _replay_summary(script_run: dict) -> dict:
    metrics = script_run.get("metrics")
    if not isinstance(metrics, dict):
        return {
            "available": False,
            "success": bool(script_run.get("success")) if script_run else None,
            "returncode": script_run.get("returncode") if script_run else None,
            "metrics_path": script_run.get("metrics_path") if script_run else None,
            "llm_calls": None,
            "total_seconds": None,
            "pure_execution_seconds": None,
            "selector_lookup_seconds": None,
            "visual_coordinate_clicks": None,
            "action_count": None,
            "final_state": {},
        }
    return {
        "available": True,
        "success": bool(script_run.get("success")),
        "returncode": script_run.get("returncode"),
        "metrics_path": script_run.get("metrics_path"),
        "llm_calls": metrics.get("llm_calls"),
        "total_seconds": metrics.get("total_seconds"),
        "pure_execution_seconds": metrics.get("pure_execution_seconds"),
        "selector_lookup_count": metrics.get("selector_lookup_count"),
        "selector_lookup_seconds": metrics.get("selector_lookup_seconds"),
        "visual_coordinate_clicks": metrics.get("visual_coordinate_clicks"),
        "action_delay_seconds": metrics.get("action_delay_seconds"),
        "postconditions": metrics.get("postconditions") or [],
        "action_count": len(metrics.get("actions") or []),
        "final_state": metrics.get("final_state") or {},
    }


def _compare_capture_and_replay(
    *,
    task: str,
    captured_actions: list[dict],
    capture_assertions: dict,
    success_assertions: list[dict],
    replay: dict,
) -> dict:
    issues: list[str] = []
    capture_passed = bool(capture_assertions.get("passed"))
    replay_available = bool(replay.get("available"))
    replay_success = bool(replay.get("success")) if replay_available else False
    if capture_assertions and not capture_passed:
        issues.append("capture assertions failed")
    if replay_available and not replay_success:
        issues.append("generated replay failed")
    llm_calls = replay.get("llm_calls") if replay_available else None
    if isinstance(llm_calls, dict) and _float_or_zero(llm_calls.get("total")) != 0:
        issues.append("generated replay used LLM calls")

    final_capture_state = _final_state_from_capture(captured_actions)
    final_replay_state = replay.get("final_state") if replay_available else {}
    postcondition_checks = _core_postcondition_checks(
        task=task,
        captured_actions=captured_actions,
        success_assertions=success_assertions,
        replay_postconditions=replay.get("postconditions") or [],
        final_capture_state=final_capture_state,
        final_replay_state=final_replay_state if isinstance(final_replay_state, dict) else {},
    )
    issues.extend(check["reason"] for check in postcondition_checks if not check["passed"])
    return {
        "passed": not issues,
        "issues": issues,
        "capture_passed": capture_passed,
        "replay_success": replay_success if replay_available else None,
        "replay_zero_llm": (
            _float_or_zero(llm_calls.get("total")) == 0 if isinstance(llm_calls, dict) else None
        ),
        "postconditions": postcondition_checks,
    }


def _core_postcondition_checks(
    *,
    task: str,
    captured_actions: list[dict],
    success_assertions: list[dict],
    replay_postconditions: list[dict],
    final_capture_state: dict,
    final_replay_state: dict,
) -> list[dict]:
    checks: list[dict] = []
    capture_url = str(final_capture_state.get("url") or "")
    replay_url = str(final_replay_state.get("url") or "")
    if "/projects/" in capture_url:
        checks.append(
            {
                "type": "url_contains",
                "value": "/projects/",
                "passed": "/projects/" in replay_url,
                "reason": "replay final URL does not contain /projects/",
            }
        )
    checks.extend(_state_postcondition_checks(captured_actions, replay_postconditions))
    checks.extend(_success_assertion_checks(success_assertions, final_replay_state))

    expected_texts = _expected_visible_texts(task, captured_actions, final_capture_state)
    replay_text = " ".join(
        str(final_replay_state.get(key) or "")
        for key in ["text_excerpt", "title", "url"]
    )
    for text in expected_texts:
        checks.append(
            {
                "type": "visible_text_contains",
                "value": text,
                "passed": text in replay_text,
                "reason": f"replay final state does not contain expected text {text!r}",
            }
        )
    return checks


def _state_postcondition_checks(
    captured_actions: list[dict],
    replay_postconditions: list[dict],
) -> list[dict]:
    required_types = _dedupe(
        str(action.get("state_postcondition", {}).get("type") or "")
        for action in captured_actions
        if isinstance(action.get("state_postcondition"), dict)
    )
    passed_types = {
        str(item.get("type") or "")
        for item in replay_postconditions
        if isinstance(item, dict) and item.get("passed")
    }
    checks: list[dict] = []
    for postcondition_type in required_types:
        if not postcondition_type:
            continue
        checks.append(
            {
                "type": "state_postcondition",
                "value": postcondition_type,
                "passed": postcondition_type in passed_types,
                "reason": f"replay did not record passing state postcondition {postcondition_type!r}",
            }
        )
    return checks


def _success_assertion_checks(
    success_assertions: list[dict],
    final_replay_state: dict,
) -> list[dict]:
    checks: list[dict] = []
    replay_url = str(final_replay_state.get("url") or "")
    replay_title = str(final_replay_state.get("title") or "")
    for assertion in success_assertions:
        if not isinstance(assertion, dict):
            continue
        assertion_type = assertion.get("type")
        value = str(assertion.get("value") or "")
        if not value:
            continue
        if assertion_type == "url_contains":
            checks.append(
                {
                    "type": assertion_type,
                    "value": value,
                    "passed": value in replay_url,
                    "reason": f"replay final URL does not contain {value!r}",
                }
            )
        elif assertion_type == "title_contains":
            checks.append(
                {
                    "type": assertion_type,
                    "value": value,
                    "passed": value in replay_title,
                    "reason": f"replay final title does not contain {value!r}",
                }
            )
    return checks


def _expected_visible_texts(
    task: str,
    captured_actions: list[dict],
    final_capture_state: dict,
) -> list[str]:
    values: list[str] = []
    for action in captured_actions:
        value = str(action.get("value") or "").strip()
        if value and (
            action.get("type") == "input"
            or (
                isinstance(action.get("postcondition"), dict)
                and action["postcondition"].get("type") == "editable_text_equals"
            )
        ):
            values.append(value)
        state_postcondition = action.get("state_postcondition")
        if isinstance(state_postcondition, dict) and state_postcondition.get("values"):
            values.extend(str(value).strip() for value in state_postcondition["values"])
    if not values:
        values.extend(_extract_cjk_runs(task))
    final_text = str(final_capture_state.get("text_excerpt") or "")
    return _dedupe(value for value in values if value and value in final_text)


def _extract_cjk_runs(text: str) -> list[str]:
    import re

    return re.findall(r"[\u4e00-\u9fff]{2,}", text)


def _final_state_from_capture(captured_actions: list[dict]) -> dict:
    if not captured_actions:
        return {}
    final = captured_actions[-1]
    return {
        "url": final.get("after_url"),
        "title": final.get("after_title"),
        "text_excerpt": final.get("after_text_excerpt"),
    }


def _iter_action_timing_dicts(
    captured_actions: list[dict],
    generation_trace: list[dict],
) -> Iterable[dict]:
    yielded_from_actions = False
    for action in captured_actions:
        timings = action.get("timings")
        if isinstance(timings, dict):
            yielded_from_actions = True
            yield timings
    if yielded_from_actions:
        return
    for entry in generation_trace:
        action = entry.get("captured_action") if isinstance(entry, dict) else None
        timings = action.get("timings") if isinstance(action, dict) else None
        if isinstance(timings, dict):
            yield timings


def _sum_timing_buckets(timing_dicts: Iterable[dict]) -> dict:
    totals: dict[str, float] = {}
    for timings in timing_dicts:
        for key, value in timings.items():
            totals[key] = totals.get(key, 0.0) + _float_or_zero(value)
    return {key: round(value, 4) for key, value in sorted(totals.items())}


def _count_trace_replans(generation_trace: list[dict]) -> int:
    return sum(
        1
        for entry in generation_trace
        if isinstance(entry, dict) and entry.get("event") == "replan"
    )


def _dedupe(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _float_or_zero(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0

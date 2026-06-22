from __future__ import annotations

from copy import deepcopy
import re
from typing import Any


KNOWN_CATEGORIES = {
    "selector_miss",
    "selector_not_unique",
    "wrong_target",
    "ambiguous_target",
    "auth_required",
    "security_challenge",
    "postcondition_failed",
    "state_unchanged",
    "network_or_load_delay",
    "visual_mapping_untrusted",
    "runtime_exception",
    "task_unsatisfiable",
    "budget_exhausted",
}


def normalize_failure_context(
    *,
    step: dict | None = None,
    error_type: str = "",
    error: str = "",
    state: dict | None = None,
    selectors: list | None = None,
    postcondition: dict | None = None,
    screenshot: str | None = None,
    retry_count: int | None = None,
    before_state: dict | None = None,
    after_state: dict | None = None,
    timings: dict | None = None,
    llm_calls: dict | None = None,
    artifact_paths: dict | None = None,
    **extra: Any,
) -> dict:
    context = {
        "schema_version": 1,
        "step": step or {},
        "error_type": error_type or "",
        "error": error or "",
        "state": state or {},
        "selectors": selectors or [],
        "postcondition": postcondition or {},
        "screenshot": screenshot,
        "retry_count": retry_count or 0,
        "before_state": before_state or {},
        "after_state": after_state or {},
        "timings": timings or {},
        "llm_calls": llm_calls or {},
        "artifact_paths": artifact_paths or {},
    }
    for key, value in extra.items():
        if value is not None:
            context[key] = value
    return context


def context_from_capture_failure(raw: dict) -> dict:
    step = _dict(raw.get("step"))
    postcondition = _dict(raw.get("postcondition") or raw.get("expected_state"))
    selectors = step.get("fallback_selectors") or step.get("selector_candidates") or []
    return normalize_failure_context(
        step=step,
        error_type=str(raw.get("error_type") or ""),
        error=str(raw.get("error") or raw.get("original_error") or ""),
        state=_dict(raw.get("state")),
        selectors=list(selectors) if isinstance(selectors, list) else [],
        postcondition=postcondition,
        screenshot=raw.get("screenshot"),
        retry_count=_int(raw.get("retry_count")),
        before_state=_dict(raw.get("before_state")),
        after_state=_dict(raw.get("after_state")),
        timings=_dict(raw.get("timings")),
        llm_calls=_dict(raw.get("llm_calls")),
        artifact_paths=_dict(raw.get("artifact_paths")),
        replan_reason=raw.get("replan_reason"),
        original_error=raw.get("original_error"),
        candidate_count=raw.get("candidate_count"),
        raw_candidate_count=raw.get("raw_candidate_count"),
        top_candidates=raw.get("top_candidates") if isinstance(raw.get("top_candidates"), list) else [],
        expected_state=raw.get("expected_state") if isinstance(raw.get("expected_state"), dict) else {},
    )


def context_from_script_run(script_run: dict, *, actions: list[dict] | None = None) -> dict:
    metrics = _dict(script_run.get("metrics"))
    completed = metrics.get("actions") if isinstance(metrics.get("actions"), list) else []
    current_action = _dict(metrics.get("current_action"))
    if not current_action and actions:
        current_action = _next_action(actions, completed)
    screenshot = None
    script_path = script_run.get("script_path")
    if script_path:
        screenshot = str(script_path).rsplit("/", 1)[0] + "/outputs/generated_script_failure.png"
    return normalize_failure_context(
        step=current_action,
        error_type=_error_type(script_run, metrics),
        error=str(metrics.get("error") or script_run.get("stderr") or ""),
        state=_dict(metrics.get("final_state")),
        selectors=current_action.get("fallback_selectors") or [],
        postcondition=_dict(current_action.get("postcondition") or current_action.get("state_postcondition")),
        screenshot=screenshot,
        retry_count=0,
        timings={
            "total_seconds": metrics.get("total_seconds"),
            "selector_lookup_seconds": metrics.get("selector_lookup_seconds"),
            "action_delay_seconds": metrics.get("action_delay_seconds"),
        },
        llm_calls=_dict(metrics.get("llm_calls")),
        artifact_paths={
            "script_path": script_run.get("script_path"),
            "metrics_path": script_run.get("metrics_path"),
        },
        returncode=script_run.get("returncode"),
        stdout=script_run.get("stdout"),
        stderr=script_run.get("stderr"),
    )


def classify_failure(context: dict) -> dict:
    ctx = normalize_failure_context(**_classification_input(context))
    category, confidence, recoverable, strategy, evidence = _classify(ctx)
    return {
        "schema_version": 1,
        "category": category,
        "confidence": confidence,
        "recoverable": recoverable,
        "recommended_strategy": strategy,
        "evidence": evidence,
        "llm_escalation_allowed": bool(confidence < 0.75 and recoverable),
    }


def _classify(ctx: dict) -> tuple[str, float, bool, str, list[str]]:
    text = _combined_text(ctx)
    state_text = _state_text(ctx.get("state") or {})
    postcondition = ctx.get("postcondition") or {}
    step = ctx.get("step") or {}
    candidates = ctx.get("top_candidates") or []

    if _contains_security_challenge(text, state_text):
        return ("security_challenge", 0.98, True, "wait_for_manual_verification", ["security challenge marker found"])
    if _contains_auth_required(ctx, state_text):
        return ("auth_required", 0.96, True, "wait_for_login", ["login/authentication marker found"])
    if _contains_budget_exhaustion(text, ctx):
        return ("budget_exhausted", 0.95, False, "stop_with_budget_report", ["timeout or budget exhaustion marker found"])
    if _contains_visual_mapping_untrusted(text, step):
        return ("visual_mapping_untrusted", 0.9, True, "retry_visual_grounding_or_requery", ["visual mapping evidence is not trusted"])
    if _contains_selector_not_unique(text, candidates):
        return ("selector_not_unique", 0.9, True, "narrow_selector", ["selector matched multiple candidates"])
    if _contains_ambiguous_target(text, candidates):
        return ("ambiguous_target", 0.82, True, "ask_planner_for_disambiguated_target", ["multiple plausible targets"])
    if _contains_selector_miss(ctx, text):
        return ("selector_miss", 0.9, True, "refresh_requery_then_fallback_selectors", ["selector lookup failed"])

    visual_issue = _visual_text_issue(ctx, postcondition)
    if visual_issue in {"obscured_by_overlay", "overlay_obscured", "blocked_by_overlay", "panel_obscured"}:
        return ("wrong_target", 0.86, True, "close_transient_overlay_then_verify", [f"visual text check issue={visual_issue}"])
    if visual_issue in {"tofu_boxes", "missing_glyph", "missing_font", "unreadable"}:
        return ("task_unsatisfiable", 0.82, False, "stop_with_unsatisfied_evidence", [f"visual text check issue={visual_issue}"])

    if _contains_network_or_load_delay(text, state_text):
        return ("network_or_load_delay", 0.84, True, "targeted_wait_then_retry", ["loading or timeout marker found"])
    if _contains_postcondition_failed(ctx, text, postcondition):
        if _state_changed(ctx):
            return ("wrong_target", 0.78, True, "requery_target_and_replay_postcondition", ["state changed but postcondition still failed"])
        return ("postcondition_failed", 0.88, True, "replan_from_failed_postcondition", ["postcondition evidence present"])
    if _states_equal(ctx.get("before_state"), ctx.get("after_state")) and (ctx.get("before_state") or ctx.get("after_state")):
        return ("state_unchanged", 0.82, True, "retry_or_replan_action_effect", ["before_state equals after_state"])
    if _runtime_exception(text, ctx):
        return ("runtime_exception", 0.74, True, "inspect_exception_or_escalate", ["unclassified runtime exception"])
    return ("runtime_exception", 0.55, True, "classify_with_llm_if_budget_allows", ["no high-confidence rule matched"])


def _classification_input(context: dict) -> dict:
    allowed = {
        "step",
        "error_type",
        "error",
        "state",
        "selectors",
        "postcondition",
        "screenshot",
        "retry_count",
        "before_state",
        "after_state",
        "timings",
        "llm_calls",
        "artifact_paths",
    }
    data = {key: deepcopy(context.get(key)) for key in allowed}
    for key in context:
        if key not in allowed:
            data[key] = deepcopy(context.get(key))
    return data


def _combined_text(ctx: dict) -> str:
    parts = [
        ctx.get("error_type"),
        ctx.get("error"),
        ctx.get("replan_reason"),
        ctx.get("original_error"),
        ctx.get("stderr"),
        ctx.get("stdout"),
    ]
    return " ".join(str(part or "") for part in parts).lower()


def _state_text(state: dict) -> str:
    return " ".join(str(state.get(key) or "") for key in ("url", "title", "text_excerpt", "html_excerpt")).lower()


def _contains_security_challenge(text: str, state_text: str) -> bool:
    markers = ("verify you are human", "security verification", "checking your browser", "cloudflare", "captcha")
    return any(marker in text or marker in state_text for marker in markers)


def _contains_auth_required(ctx: dict, state_text: str) -> bool:
    if ctx.get("error_type") == "AuthenticationRequired":
        return True
    if ctx.get("replan_reason") == "auth_required":
        return True
    markers = ("log in", "/login", "authentication required", "please authenticate")
    return any(marker in state_text for marker in markers)


def _contains_budget_exhaustion(text: str, ctx: dict) -> bool:
    if str(ctx.get("error_type") or "").lower() in {"timeoutexpired", "budgetexhausted"}:
        return True
    return "budget exhausted" in text or "maximum debug" in text or "timed out after" in text


def _contains_visual_mapping_untrusted(text: str, step: dict) -> bool:
    if "visual fallback" in text and (
        "outside the screenshot" in text
        or "did not report the visible label" in text
        or ("expected" in text and "visible label" in text)
        or "untrusted" in text
    ):
        return True
    return bool(step.get("visual_position") and step.get("selector_backfilled") is False)


def _contains_selector_not_unique(text: str, candidates: list) -> bool:
    if any(marker in text for marker in ("strict mode violation", "matched multiple", "not unique", "multiple elements")):
        return True
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        for metadata in candidate.get("selector_metadata") or []:
            if isinstance(metadata, dict) and int(metadata.get("match_count") or 0) > 1:
                return True
    return False


def _contains_ambiguous_target(text: str, candidates: list) -> bool:
    if "ambiguous" in text:
        return True
    visible = [candidate for candidate in candidates if isinstance(candidate, dict) and candidate.get("is_visible", True)]
    labels = {
        str(candidate.get("text") or candidate.get("accessible_name") or "").strip().lower()
        for candidate in visible
    }
    labels.discard("")
    return len(visible) >= 3 and len(labels) <= 2


def _contains_selector_miss(ctx: dict, text: str) -> bool:
    if ctx.get("replan_reason") == "selector_miss":
        return True
    if "element lookup failed" in text or "no selectors provided" in text:
        return True
    if "no selectors available" in text or ("target" in text and "failed after retry" in text):
        return True
    if ctx.get("postcondition"):
        return False
    step_type = str((ctx.get("step") or {}).get("type") or "")
    selector_actions = {"click", "double_click", "input", "select", "upload", "set_range", "set_timecode", "drag"}
    return step_type in selector_actions and not ctx.get("selectors") and "unsupported action type" not in text


def _visual_text_issue(ctx: dict, postcondition: dict) -> str:
    sources = [
        postcondition.get("visual_text_check") if isinstance(postcondition, dict) else None,
        (ctx.get("state") or {}).get("visual_text_check"),
        (ctx.get("expected_state") or {}).get("visual_text_check") if isinstance(ctx.get("expected_state"), dict) else None,
    ]
    for source in sources:
        if isinstance(source, dict):
            issue = str(source.get("issue") or "").strip().lower()
            if issue:
                return issue
    match = re.search(r'"issue"\s*:\s*"([^"]+)"', _combined_text(ctx))
    return match.group(1).lower() if match else ""


def _contains_network_or_load_delay(text: str, state_text: str) -> bool:
    loading_markers = ("loading", "spinner", "please wait", "processing", "uploading", "networkidle")
    timeout_markers = ("navigation timeout", "load timeout", "wait timeout", "did not appear", "not loaded")
    return any(marker in state_text for marker in loading_markers) or any(marker in text for marker in timeout_markers)


def _contains_postcondition_failed(ctx: dict, text: str, postcondition: dict) -> bool:
    if postcondition:
        return True
    return "postcondition failed" in text or "state postcondition failed" in text or "assertion failed" in text


def _state_changed(ctx: dict) -> bool:
    before = ctx.get("before_state") or {}
    after = ctx.get("after_state") or {}
    return bool(before and after and not _states_equal(before, after))


def _states_equal(before: Any, after: Any) -> bool:
    return _dict(before) == _dict(after)


def _runtime_exception(text: str, ctx: dict) -> bool:
    return bool(ctx.get("error_type") or "traceback" in text or "runtimeerror" in text or "valueerror" in text)


def _next_action(actions: list[dict], completed: list) -> dict:
    completed_count = len(completed)
    if completed_count < len(actions):
        return dict(actions[completed_count])
    return {}


def _error_type(script_run: dict, metrics: dict) -> str:
    error = str(metrics.get("error") or script_run.get("stderr") or "")
    match = re.search(r"([A-Za-z_][A-Za-z0-9_]*(?:Error|Exception|Required|Expired))\s*:", error)
    if match:
        return match.group(1)
    return "RuntimeError" if script_run.get("returncode") else ""


def _dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0

from __future__ import annotations

import json
import re
from pathlib import Path
from time import perf_counter
from typing import Any
from urllib.parse import urlparse
from urllib.parse import urlunparse

from app.debug.failure_context import classify_failure
from app.debug.failure_context import context_from_capture_failure
from app.generation.candidate_compactor import build_grounding_candidates
from app.generation.exceptions import AuthenticationRequired
from app.generation.planner import ActionPlan
from app.generation.planner import ActionStep
from app.generation.planner import Planner
from app.generation.selector_grounder import SelectorGrounder
from app.generation.visual_fallback import VisualFallbackGrounder
from app.generation.visual_fallback import VisualTextVerifier
from app.resilience.executor import ResilientActionExecutor
from app.runtime.drission_runtime import DrissionRuntime


class StateNotReachedError(RuntimeError):
    reason = "state_not_reached"

    def __init__(self, message: str, *, expected: dict | None = None) -> None:
        super().__init__(message)
        self.expected = expected or {}


class CaptureRunner:
    def __init__(
        self,
        *,
        runtime: DrissionRuntime | None = None,
        grounder: SelectorGrounder | None = None,
        planner: Planner | None = None,
        visual_grounder: VisualFallbackGrounder | None = None,
        visual_text_verifier: VisualTextVerifier | None = None,
        output_dir: Path | None = None,
        wait_for_login: bool = False,
        login_timeout_seconds: int = 300,
        max_replans: int = 1,
        action_delay_seconds: float = 0,
        close_on_finish: bool = True,
        debug_artifacts: bool = False,
    ) -> None:
        self.runtime = runtime or DrissionRuntime()
        self.grounder = grounder or SelectorGrounder()
        self.planner = planner
        self.visual_grounder = visual_grounder or VisualFallbackGrounder()
        self.visual_text_verifier = visual_text_verifier or VisualTextVerifier()
        self.output_dir = output_dir
        self.wait_for_login = wait_for_login
        self.login_timeout_seconds = login_timeout_seconds
        self.max_replans = max_replans
        self.action_delay_seconds = max(0.0, float(action_delay_seconds))
        self.close_on_finish = close_on_finish
        self.debug_artifacts = debug_artifacts
        self.executor = ResilientActionExecutor(
            self.runtime,
            snapshot_fn=self._snapshot_with_retry,
        )

    def run(self, plan: ActionPlan) -> list[dict]:
        output_dir = self.output_dir
        if output_dir:
            (output_dir / "dom_snapshots").mkdir(parents=True, exist_ok=True)
            (output_dir / "screenshots").mkdir(parents=True, exist_ok=True)

        captured_actions: list[dict] = []
        trace: list[dict] = []
        context = {"task": plan.task, "completed_actions": captured_actions}
        task = plan.task
        steps = list(plan.steps)
        cursor = 0
        replan_count = 0
        pending_visual_text_postcondition: dict | None = None

        try:
            while cursor < len(steps):
                step = steps[cursor]
                step_index = len(trace)
                step_dict = step.to_dict()
                before_state = self.runtime.state() if self.runtime.page is not None else {}
                started = perf_counter()

                if step.type == "goto":
                    self.runtime.goto(step.url or "")
                    after_state = self._ensure_authenticated(step_index, step_dict)
                    after_state = self._pause_after_action(step_index, step_dict, after_state)
                    try:
                        self._verify_navigation_state(step_dict, after_state)
                    except Exception as exc:
                        raw_candidates = self._safe_snapshot()
                        candidates = build_grounding_candidates(step_dict, raw_candidates)
                        self._write_failure_context(
                            step_index=step_index,
                            step=step_dict,
                            raw_candidates=raw_candidates,
                            candidates=candidates,
                            error=exc,
                        )
                        if replan_count < self.max_replans:
                            replan = self._try_replan(
                                task=task,
                                step_index=step_index,
                                step=step_dict,
                                candidates=candidates,
                                error=exc,
                                completed_actions=captured_actions,
                            )
                            if replan is not None:
                                replan_count += 1
                                steps = list(replan.steps)
                                cursor = 0
                                context = {"task": plan.task, "completed_actions": captured_actions}
                                continue
                        raise
                    action = {
                        "step_index": step_index,
                        "type": step.type,
                        "target": step.target,
                        "comment": step.comment,
                        "url": step.url,
                        "chosen_selector": None,
                        "fallback_selectors": [],
                        "before_url": before_state.get("url"),
                        "after_url": after_state.get("url"),
                        "before_title": before_state.get("title"),
                        "after_title": after_state.get("title"),
                        "after_text_excerpt": after_state.get("text_excerpt"),
                    }
                    visual_text_recovery = self._try_verify_visual_text_recovery(
                        step_index=step_index,
                        step=step_dict,
                        postcondition=pending_visual_text_postcondition,
                        current_state=after_state,
                    )
                    if visual_text_recovery is not None:
                        _mark_deferred_visual_text_origin_passed(
                            captured_actions,
                            pending_visual_text_postcondition,
                            visual_text_recovery,
                        )
                        action["postcondition"] = pending_visual_text_postcondition
                        action["postcondition_passed"] = True
                        action["rendered_text_readable"] = True
                        action["visual_text_check"] = visual_text_recovery
                        action["visual_text_recovery_passed"] = True
                        captured_actions.append(action)
                        trace.append(_trace_entry(step_index, step_dict, None, action, started))
                        self._persist_progress(captured_actions, trace)
                        return captured_actions
                    captured_actions.append(action)
                    trace.append(_trace_entry(step_index, step_dict, None, action, started))
                    self._persist_progress(captured_actions, trace)
                    cursor += 1
                    continue

                if step.type == "wait":
                    seconds = float(step.value or 1)
                    self.runtime.wait(seconds)
                    after_state = self._ensure_authenticated(step_index, step_dict)
                    after_state = self._pause_after_action(step_index, step_dict, after_state)
                    try:
                        state_postcondition = _build_state_postcondition(
                            step_dict,
                            completed_actions=captured_actions,
                        )
                        after_state = self._verify_state_postcondition(
                            state_postcondition,
                            current_state=after_state,
                        )
                        state_postcondition_passed = state_postcondition is not None
                    except Exception as exc:
                        raw_candidates = self._safe_snapshot()
                        candidates = build_grounding_candidates(step_dict, raw_candidates)
                        self._write_failure_context(
                            step_index=step_index,
                            step=step_dict,
                            raw_candidates=raw_candidates,
                            candidates=candidates,
                            error=exc,
                        )
                        if replan_count < self.max_replans:
                            replan = self._try_replan(
                                task=task,
                                step_index=step_index,
                                step=step_dict,
                                candidates=candidates,
                                error=exc,
                                completed_actions=captured_actions,
                            )
                            if replan is not None:
                                replan_count += 1
                                steps = list(replan.steps)
                                cursor = 0
                                context = {"task": plan.task, "completed_actions": captured_actions}
                                continue
                        raise
                    action = {
                        "step_index": step_index,
                        "type": step.type,
                        "target": step.target,
                        "comment": step.comment,
                        "seconds": seconds,
                        "chosen_selector": None,
                        "fallback_selectors": [],
                        "state_postcondition": state_postcondition,
                        "state_postcondition_passed": state_postcondition_passed,
                        "before_url": before_state.get("url"),
                        "after_url": after_state.get("url"),
                        "before_title": before_state.get("title"),
                        "after_title": after_state.get("title"),
                        "after_text_excerpt": after_state.get("text_excerpt"),
                    }
                    visual_text_recovery = self._try_verify_visual_text_recovery(
                        step_index=step_index,
                        step=step_dict,
                        postcondition=pending_visual_text_postcondition,
                        current_state=after_state,
                    )
                    if visual_text_recovery is not None:
                        _mark_deferred_visual_text_origin_passed(
                            captured_actions,
                            pending_visual_text_postcondition,
                            visual_text_recovery,
                        )
                        action["postcondition"] = pending_visual_text_postcondition
                        action["postcondition_passed"] = True
                        action["rendered_text_readable"] = True
                        action["visual_text_check"] = visual_text_recovery
                        action["visual_text_recovery_passed"] = True
                        captured_actions.append(action)
                        trace.append(_trace_entry(step_index, step_dict, None, action, started))
                        self._persist_progress(captured_actions, trace)
                        return captured_actions
                    captured_actions.append(action)
                    trace.append(_trace_entry(step_index, step_dict, None, action, started))
                    self._persist_progress(captured_actions, trace)
                    cursor += 1
                    continue

                if step.type == "press_key":
                    self._ensure_authenticated(step_index, step_dict)
                    try:
                        execution = self.executor.execute(step_dict, [])
                        self._pause_after_action(step_index, step_dict)
                        after_state = self._ensure_authenticated(step_index, step_dict)
                    except Exception as exc:
                        self._write_failure_context(
                            step_index=step_index,
                            step=step_dict,
                            raw_candidates=[],
                            candidates=[],
                            error=exc,
                        )
                        if replan_count < self.max_replans:
                            replan = self._try_replan(
                                task=task,
                                step_index=step_index,
                                step=step_dict,
                                candidates=[],
                                error=exc,
                                completed_actions=captured_actions,
                            )
                            if replan is not None:
                                replan_count += 1
                                steps = list(replan.steps)
                                cursor = 0
                                context = {"task": plan.task, "completed_actions": captured_actions}
                                continue
                        raise
                    action = {
                        "step_index": step_index,
                        "type": step.type,
                        "target": step.target,
                        "comment": step.comment,
                        "value": step.value,
                        "chosen_selector": execution.used_selector,
                        "fallback_selectors": [],
                        "fallback_level": execution.fallback_level,
                        "retry_count": execution.retry_count,
                        "execution_notes": execution.notes,
                        "before_url": before_state.get("url"),
                        "after_url": after_state.get("url"),
                        "before_title": before_state.get("title"),
                        "after_title": after_state.get("title"),
                        "after_text_excerpt": after_state.get("text_excerpt"),
                    }
                    captured_actions.append(action)
                    trace.append(_trace_entry(step_index, step_dict, None, action, started))
                    self._persist_progress(captured_actions, trace)
                    cursor += 1
                    continue

                self._ensure_authenticated(step_index, step_dict)
                raw_candidates = self._snapshot_with_retry()
                candidates = build_grounding_candidates(step_dict, raw_candidates)
                if output_dir:
                    _write_json(
                        output_dir / "dom_snapshots" / f"step_{step_index:02d}.json",
                        {"step": step_dict, "candidates": candidates},
                    )
                    if self.debug_artifacts:
                        _write_json(
                            output_dir / "dom_snapshots" / f"raw_step_{step_index:02d}.json",
                            {"step": step_dict, "candidates": raw_candidates},
                        )

                try:
                    postcondition = None
                    state_postcondition = None
                    visual_text_check = None
                    rendered_text_readable = False
                    postcondition_passed = False
                    state_postcondition_passed = False
                    grounding = self.grounder.ground(
                        step=step_dict,
                        candidates=candidates,
                        context=context,
                    )
                    selectors = grounding["selectors"]
                    selector_metadata = grounding.get("selector_metadata") or []
                    selector_indexes = _selector_indexes(selector_metadata)
                    execution = self.executor.execute(
                        step_dict,
                        selectors,
                        selector_indexes=selector_indexes,
                    )
                    chosen_selector = execution.used_selector
                    chosen_metadata = _selector_metadata_for(
                        selector_metadata, chosen_selector
                    )
                    self._pause_after_action(step_index, step_dict)
                    after_state = self._ensure_authenticated(step_index, step_dict)
                    if step.type == "click":
                        self._verify_navigation_state(step_dict, after_state)
                        after_state = self._verify_click_state_transition(
                            step_dict,
                            before_state=before_state,
                            current_state=after_state,
                        )
                    postcondition = _build_postcondition(
                        step_dict, grounding.get("candidate") or {}
                    )
                    self._verify_postcondition(postcondition)
                    if _should_defer_visual_text_check(
                        postcondition,
                        remaining_steps=steps[cursor + 1 :],
                    ):
                        pending_visual_text_postcondition = {
                            **(postcondition or {}),
                            "_defer_until_commit": True,
                            "_origin_step_index": step_index,
                        }
                        visual_text_check = None
                    else:
                        visual_text_check = self._verify_visual_text_rendering(
                            step_index=step_index,
                            step=step_dict,
                            postcondition=postcondition,
                            current_state=after_state,
                        )
                    rendered_text_readable = visual_text_check is not None
                    state_postcondition = _build_state_postcondition(
                        step_dict,
                        completed_actions=captured_actions,
                    )
                    after_state = self._verify_state_postcondition(
                        state_postcondition,
                        current_state=after_state,
                    )
                    postcondition_passed = postcondition is not None
                    state_postcondition_passed = state_postcondition is not None
                except Exception as exc:
                    pending_visual_text_postcondition = (
                        _visual_text_postcondition_from_error(exc)
                        or pending_visual_text_postcondition
                    )
                    self._write_failure_context(
                        step_index=step_index,
                        step=step_dict,
                        raw_candidates=raw_candidates,
                        candidates=candidates,
                        error=exc,
                    )
                    prefer_visual = _should_use_visual_fallback(
                        error=exc,
                        step=step_dict,
                        state=self.runtime.state(),
                        replan_count=replan_count,
                    )
                    if not prefer_visual and replan_count < self.max_replans:
                        replan = self._try_replan(
                            task=task,
                            step_index=step_index,
                            step=step_dict,
                            candidates=candidates,
                            error=exc,
                            completed_actions=captured_actions,
                        )
                        if replan is not None:
                            replan_count += 1
                            steps = list(replan.steps)
                            cursor = 0
                            context = {"task": plan.task, "completed_actions": captured_actions}
                            continue
                    visual_action = self._try_visual_fallback(
                        task=task,
                        step_index=step_index,
                        step=_visual_fallback_step(task, step_dict, exc),
                        error=exc,
                        completed_actions=captured_actions,
                        before_state=before_state,
                        started=started,
                    )
                    if visual_action is not None:
                        captured_actions.append(visual_action)
                        trace.append(_trace_entry(step_index, step_dict, None, visual_action, started))
                        self._persist_progress(captured_actions, trace)
                        cursor += 1
                        continue
                    raise
                action = {
                    "step_index": step_index,
                    "type": step.type,
                    "target": step.target,
                    "comment": step.comment,
                    "chosen_selector": chosen_selector,
                    "fallback_selectors": selectors,
                    "fallback_level": execution.fallback_level,
                    "retry_count": execution.retry_count,
                    "refreshed_snapshot": execution.refreshed_snapshot,
                    "execution_notes": execution.notes,
                    "selector_metadata": selector_metadata,
                    "chosen_selector_index": chosen_metadata.get("index"),
                    "chosen_selector_match_count": chosen_metadata.get("match_count"),
                    "chosen_selector_unique": chosen_metadata.get("unique"),
                    "candidate_id": grounding["candidate_id"],
                    "grounding_reason": grounding["reason"],
                    "semantic_type": grounding.get("candidate", {}).get("semantic_type"),
                    "action_allowed": grounding.get("candidate", {}).get("action_allowed"),
                    "candidate_text": grounding.get("candidate", {}).get("text"),
                    "candidate_accessible_name": grounding.get("candidate", {}).get(
                        "accessible_name"
                    ),
                    "candidate_aria_label": grounding.get("candidate", {}).get("aria_label"),
                    "candidate_role": grounding.get("candidate", {}).get("role"),
                    "candidate_aria_selected": grounding.get("candidate", {}).get(
                        "aria_selected"
                    ),
                    "candidate_data_state": grounding.get("candidate", {}).get("data_state"),
                    "postcondition": postcondition,
                    "postcondition_passed": postcondition_passed,
                    "rendered_text_readable": rendered_text_readable,
                    "visual_text_check": visual_text_check,
                    "state_postcondition": state_postcondition,
                    "state_postcondition_passed": state_postcondition_passed,
                    "context_text": grounding.get("candidate", {}).get("context_text"),
                    "result_rank": grounding.get("candidate", {}).get("result_rank"),
                    "related_item_rank": grounding.get("candidate", {}).get("related_item_rank"),
                    "related_item_context": grounding.get("candidate", {}).get("related_item_context"),
                    "upload_label": grounding.get("candidate", {}).get("upload_label"),
                    "upload_kind": grounding.get("candidate", {}).get("upload_kind"),
                    "value": step.value,
                    "path": step.path,
                    "select_by": step.select_by,
                    "delta_x": step.delta_x,
                    "delta_y": step.delta_y,
                    "duration": step.duration,
                    "before_url": before_state.get("url"),
                    "after_url": after_state.get("url"),
                    "before_title": before_state.get("title"),
                    "after_title": after_state.get("title"),
                    "after_text_excerpt": after_state.get("text_excerpt"),
                }
                visual_text_recovery = self._try_verify_visual_text_recovery(
                    step_index=step_index,
                    step=step_dict,
                    postcondition=pending_visual_text_postcondition,
                    current_state=after_state,
                )
                if visual_text_recovery is not None:
                    _mark_deferred_visual_text_origin_passed(
                        captured_actions,
                        pending_visual_text_postcondition,
                        visual_text_recovery,
                    )
                    action["postcondition"] = pending_visual_text_postcondition
                    action["postcondition_passed"] = True
                    action["rendered_text_readable"] = True
                    action["visual_text_check"] = visual_text_recovery
                    action["visual_text_recovery_passed"] = True
                    captured_actions.append(action)
                    trace.append(_trace_entry(step_index, step_dict, grounding, action, started))
                    self._persist_progress(captured_actions, trace)
                    return captured_actions
                captured_actions.append(action)
                trace.append(_trace_entry(step_index, step_dict, grounding, action, started))
                self._persist_progress(captured_actions, trace)
                cursor += 1

            if pending_visual_text_postcondition is not None:
                final_postcondition = dict(pending_visual_text_postcondition)
                final_postcondition.pop("_defer_until_commit", None)
                final_state = self.runtime.state()
                final_check = self._try_verify_visual_text_recovery(
                    step_index=len(trace),
                    step={
                        "type": "wait",
                        "target": "final rendered text verification",
                        "comment": "Verify deferred visual text after all recovery steps",
                    },
                    postcondition=final_postcondition,
                    current_state=final_state,
                )
                if final_check is not None:
                    _mark_deferred_visual_text_origin_passed(
                        captured_actions,
                        pending_visual_text_postcondition,
                        final_check,
                    )
                    self._persist_progress(captured_actions, trace)
                    return captured_actions
                raise StateNotReachedError(
                    "Visual text rendering recovery did not produce readable rendered text",
                    expected=pending_visual_text_postcondition,
                )
            if output_dir:
                self._persist_progress(captured_actions, trace)
            return captured_actions
        finally:
            self._persist_progress(captured_actions, trace)
            if self.close_on_finish:
                self.runtime.close()

    def _persist_progress(self, captured_actions: list[dict], trace: list[dict]) -> None:
        if not self.output_dir:
            return
        _write_json(self.output_dir / "captured_actions.json", captured_actions)
        _write_json(self.output_dir / "generation_trace.json", trace)

    def _pause_after_action(
        self,
        step_index: int,
        step: dict,
        current_state: dict | None = None,
    ) -> dict:
        if self.action_delay_seconds <= 0:
            return current_state or {}
        target = step.get("target") or step.get("url") or step.get("type")
        print(f"Waiting {self.action_delay_seconds:g}s after step {step_index}: {target}")
        self.runtime.wait(self.action_delay_seconds)
        if self.output_dir:
            screenshot_path = (
                self.output_dir / "screenshots" / f"after_step_{step_index:02d}.png"
            )
            try:
                self.runtime.screenshot(str(screenshot_path))
            except Exception as exc:
                # An action may already have succeeded while a busy canvas or
                # renderer makes CDP Page.captureScreenshot time out. Debug
                # evidence is best-effort and must not turn that successful
                # business action into an execution failure or trigger replan.
                _write_json(
                    self.output_dir
                    / "observation_warnings"
                    / f"after_step_{step_index:02d}.json",
                    {
                        "step_index": step_index,
                        "step": step,
                        "operation": "screenshot_after_action",
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                        "screenshot": str(screenshot_path),
                    },
                )
                print(
                    f"Warning: could not capture screenshot after step {step_index}: "
                    f"{type(exc).__name__}: {exc}"
                )
        try:
            return self.runtime.state()
        except Exception:
            return current_state or {}

    def _verify_postcondition(self, postcondition: dict | None) -> None:
        if not postcondition:
            return
        if postcondition.get("type") == "editable_text_equals":
            expected = str(postcondition.get("value") or "")
            actual = ""
            for _ in range(4):
                candidate = _find_candidate_by_selectors(
                    self.runtime.snapshot(), postcondition.get("selectors") or []
                )
                actual = str((candidate or {}).get("text") or "").strip()
                if actual == expected:
                    return
                self.runtime.wait(0.5)
            raise StateNotReachedError(
                f"Postcondition failed: expected editable text {expected!r}, got {actual!r}",
                expected=postcondition,
            )
        if postcondition.get("type") == "value_equals":
            expected = str(postcondition.get("value") or "")
            candidate = _find_candidate_by_selectors(
                self.runtime.snapshot(), postcondition.get("selectors") or []
            )
            if candidate and str(candidate.get("value") or "") == expected:
                return
            raise StateNotReachedError(
                f"Postcondition failed: expected control value {expected!r}",
                expected=postcondition,
            )
        if postcondition.get("type") == "element_state_changed":
            candidate = _find_candidate_by_selectors(
                self.runtime.snapshot(), postcondition.get("selectors") or []
            )
            if candidate and _candidate_state_changed(
                postcondition.get("before") or {}, candidate
            ):
                return
            raise StateNotReachedError(
                "Postcondition failed: dragged element state did not change",
                expected=postcondition,
            )
        if postcondition.get("type") != "tab_selected":
            return
        expected = str(postcondition.get("label") or "").strip().lower()
        for candidate in self.runtime.snapshot():
            if str(candidate.get("role") or "").lower() != "tab":
                continue
            label = " ".join(
                str(candidate.get(key) or "")
                for key in ["text", "accessible_name", "aria_label"]
            ).lower()
            selected = (
                str(candidate.get("aria_selected") or "").lower() == "true"
                or str(candidate.get("data_state") or "").lower() in {"active", "selected"}
                or "data-active" in (candidate.get("data_attrs") or {})
            )
            if expected in label and selected:
                return
        raise RuntimeError(f"Postcondition failed: tab {expected!r} is not selected")

    def _verify_visual_text_rendering(
        self,
        *,
        step_index: int,
        step: dict,
        postcondition: dict | None,
        current_state: dict | None,
    ) -> dict | None:
        if not postcondition or not postcondition.get("requires_visual_text_rendering"):
            return None
        expected_text = str(postcondition.get("value") or "")
        if not expected_text:
            return None
        if not self.output_dir:
            raise StateNotReachedError(
                "Visual text rendering verification requires an output directory for screenshots",
                expected=postcondition,
            )

        check_dir = self.output_dir / "visual_text_checks"
        check_dir.mkdir(parents=True, exist_ok=True)
        screenshot_path = check_dir / f"step_{step_index:02d}.png"
        self.runtime.screenshot(str(screenshot_path))
        check = self.visual_text_verifier.verify_rendered_text(
            expected_text=expected_text,
            screenshot_path=screenshot_path,
            context={
                "step": step,
                "url": (current_state or {}).get("url"),
                "title": (current_state or {}).get("title"),
                "text_excerpt": (current_state or {}).get("text_excerpt"),
            },
        )
        result = {
            "readable": check.readable,
            "exact_text_visible": check.exact_text_visible,
            "observed_text": check.observed_text,
            "issue": check.issue,
            "reason": check.reason,
            "screenshot": str(screenshot_path),
        }
        _write_json(
            check_dir / f"step_{step_index:02d}.json",
            {
                "step_index": step_index,
                "step": step,
                "postcondition": postcondition,
                "result": result,
            },
        )
        if check.readable and check.exact_text_visible:
            return result
        raise StateNotReachedError(
            "Postcondition failed: expected editable text matched DOM but was not visibly readable",
            expected={
                **postcondition,
                "visual_text_check": result,
            },
        )

    def _try_verify_visual_text_recovery(
        self,
        *,
        step_index: int,
        step: dict,
        postcondition: dict | None,
        current_state: dict | None,
    ) -> dict | None:
        if not postcondition:
            return None
        if postcondition.get("_defer_until_commit") and not _is_text_commit_step(step):
            return None
        verification_state = current_state or {}
        if _has_transient_text_property_overlay(verification_state):
            self.executor.execute(
                {
                    "type": "press_key",
                    "value": "ESCAPE",
                    "target": "transient text property overlay",
                },
                [],
            )
            self.runtime.wait(0.2)
            verification_state = self.runtime.state()
            if _has_transient_text_property_overlay(verification_state):
                return None
        try:
            return self._verify_visual_text_rendering(
                step_index=step_index,
                step=step,
                postcondition=postcondition,
                current_state=verification_state,
            )
        except StateNotReachedError:
            return None

    def _verify_state_postcondition(
        self,
        postcondition: dict | None,
        *,
        current_state: dict | None = None,
    ) -> dict:
        if not postcondition:
            return current_state or {}
        if postcondition.get("type") == "timeline_media_present":
            state = current_state or self.runtime.state()
            for _ in range(5):
                if self.runtime.timeline_media_present():
                    return self.runtime.state()
                self.runtime.wait(2)
                state = self.runtime.state()
            raise StateNotReachedError(
                "State postcondition failed: no media clip was detected in the timeline",
                expected=postcondition,
            )
        if postcondition.get("type") == "visible_text_all":
            expected_values = [
                str(value).strip().lower()
                for value in postcondition.get("values") or []
                if str(value).strip()
            ]
            state = current_state or self.runtime.state()
            for _ in range(4):
                text = " ".join(
                    str(state.get(key) or "")
                    for key in ["text_excerpt", "html_excerpt", "title", "url"]
                ).lower()
                if expected_values and all(value in text for value in expected_values):
                    return state
                self.runtime.wait(2)
                state = self.runtime.state()
            raise StateNotReachedError(
                f"State postcondition failed: expected visible text containing all of {expected_values!r}",
                expected=postcondition,
            )
        if postcondition.get("type") != "visible_text_contains":
            return current_state or {}
        expected_values = [
            str(value).strip().lower()
            for value in postcondition.get("values") or []
            if str(value).strip()
        ]
        if not expected_values:
            return current_state or {}

        state = current_state or self.runtime.state()
        for _ in range(4):
            text = " ".join(
                str(state.get(key) or "")
                for key in ["text_excerpt", "html_excerpt", "title", "url"]
            ).lower()
            if any(value in text for value in expected_values):
                return state
            self.runtime.wait(2)
            state = self.runtime.state()

        raise StateNotReachedError(
            f"State postcondition failed: expected visible text containing one of {expected_values!r}",
            expected=postcondition,
        )

    def _verify_navigation_state(self, step: dict, state: dict) -> None:
        text = " ".join(
            str(state.get(key) or "")
            for key in ["title", "text_excerpt", "html_excerpt"]
        ).lower()
        url = str(state.get("url") or "")
        if any(marker in text for marker in ["404", "page not found", "not found on this server"]):
            raise StateNotReachedError(
                f"Navigation did not reach a usable page: {url}",
                expected={
                    "type": "navigation_reachable",
                    "url": step.get("url"),
                },
            )

    def _verify_click_state_transition(
        self,
        step: dict,
        *,
        before_state: dict,
        current_state: dict,
    ) -> dict:
        target = _semantic_target(step.get("target"))
        if "create project" not in target and "创建项目" not in target:
            return current_state
        before_url = str(before_state.get("url") or "")
        state = current_state
        for _ in range(4):
            text = _semantic_target(state.get("text_excerpt"))
            current_url = str(state.get("url") or "")
            project_url_reached = "/projects/" in current_url and current_url != before_url
            editor_ready = any(
                marker in text
                for marker in ["new project", "add files", "my files", "canvas", "新项目", "添加文件"]
            )
            if project_url_reached or editor_ready:
                return state
            self.runtime.wait(2)
            state = self.runtime.state()
        raise StateNotReachedError(
            "Click did not create or enter a project",
            expected={"type": "project_created", "target": step.get("target")},
        )

    def _snapshot_with_retry(self) -> list[dict]:
        candidates = self.runtime.snapshot()
        for _ in range(6):
            if _has_actionable_candidates(candidates):
                return candidates
            self.runtime.wait(3)
            candidates = self.runtime.snapshot()
        return candidates

    def _safe_snapshot(self) -> list[dict]:
        try:
            return self.runtime.snapshot()
        except Exception:
            return []

    def _ensure_authenticated(self, step_index: int, step: dict) -> dict:
        state = self.runtime.state()
        if _is_security_challenge(state):
            if getattr(getattr(self.runtime, "settings", None), "browser_headless", False):
                raise AuthenticationRequired(
                    "Browser security verification cannot be completed in headless mode. "
                    "Rerun with --headed and complete it manually.",
                    step_index=step_index,
                    url=state.get("url"),
                )
            if self.wait_for_login:
                self._write_auth_event(step_index=step_index, step=step, state=state)
                return self._wait_for_login_state(step_index=step_index, step=step)
            raise AuthenticationRequired(
                "Browser security verification requires manual completion in headed Chrome.",
                step_index=step_index,
                url=state.get("url"),
            )
        if not _is_auth_blocked(state):
            return state

        auto_sign_in = self._try_click_sign_in()
        if auto_sign_in:
            print(f"Opened sign-in flow automatically using {auto_sign_in}.")
            self.runtime.wait(1)
            state = self.runtime.state()
            if not _is_auth_blocked(state) and not _is_security_challenge(state):
                return state

        error = AuthenticationRequired(
            f"Authentication required at step {step_index}: {state.get('url')}",
            step_index=step_index,
            url=state.get("url"),
        )
        if not self.wait_for_login:
            self._write_failure_context(
                step_index=step_index,
                step=step,
                raw_candidates=[],
                candidates=[],
                error=error,
            )
            raise error
        self._write_auth_event(step_index=step_index, step=step, state=state)
        return self._wait_for_login_state(step_index=step_index, step=step)

    def _try_click_sign_in(self) -> str | None:
        try:
            candidates = self.runtime.snapshot()
        except Exception:
            return None
        labels = {"sign in", "signin", "log in", "login", "登录"}
        for candidate in candidates:
            candidate_labels = {
                " ".join(str(candidate.get(key) or "").lower().split())
                for key in ["text", "accessible_name", "aria_label"]
                if candidate.get(key)
            }
            if not candidate_labels.intersection(labels):
                continue
            if candidate.get("tag") not in {"button", "a"}:
                continue
            selectors = candidate.get("selector_candidates") or []
            metadata = candidate.get("selector_metadata") or []
            if not selectors:
                continue
            indexes = _selector_indexes(metadata)
            result = self.executor.click_with_fallback(
                selectors,
                "Sign In",
                selector_indexes=indexes,
            )
            return result.used_selector
        return None

    def _wait_for_login_state(self, *, step_index: int, step: dict) -> dict:
        print(
            "Manual authentication or security verification is required. "
            "Finish it in the opened browser window; "
            "the agent is monitoring the page and will continue automatically."
        )
        started = perf_counter()
        last_report_second = -1
        while perf_counter() - started < self.login_timeout_seconds:
            self.runtime.wait(2)
            state = self.runtime.state()
            elapsed = int(perf_counter() - started)
            if elapsed // 15 != last_report_second // 15:
                last_report_second = elapsed
                print(f"Waiting for login to complete... {elapsed}s")
            if _is_auth_blocked(state) or _is_security_challenge(state):
                continue
            candidates = self._snapshot_with_retry()
            if _has_actionable_candidates(candidates):
                print("Login appears complete. Continuing the captured workflow.")
                return state

        state = self.runtime.state()
        error = AuthenticationRequired(
            f"Timed out waiting for login after {self.login_timeout_seconds}s: {state.get('url')}",
            step_index=step_index,
            url=state.get("url"),
        )
        self._write_failure_context(
            step_index=step_index,
            step=step,
            raw_candidates=[],
            candidates=[],
            error=error,
        )
        raise error

    def _write_auth_event(self, *, step_index: int, step: dict, state: dict) -> None:
        if not self.output_dir:
            return
        screenshot_path = self.output_dir / "auth_events" / f"step_{step_index:02d}.png"
        try:
            self.runtime.screenshot(str(screenshot_path))
        except Exception:
            screenshot_path = None
        _write_json(
            self.output_dir / "auth_events" / f"step_{step_index:02d}.json",
            {
                "step_index": step_index,
                "step": step,
                "state": state,
                "screenshot": str(screenshot_path) if screenshot_path else None,
                "instruction": "Manual login detected; runner monitored the browser and continued after auth cleared.",
            },
        )

    def _try_replan(
        self,
        *,
        task: str,
        step_index: int,
        step: dict,
        candidates: list[dict],
        error: Exception,
        completed_actions: list[dict],
    ) -> ActionPlan | None:
        if not self.planner:
            return None
        state = self.runtime.state()
        visual_text_check = _visual_text_check_from_error(error)
        if visual_text_check:
            state["visual_text_check"] = visual_text_check
            state["visual_text_recovery_required"] = (
                "DOM text matched, but screenshot-level rendered text was not readable. "
                "Change the selected font/style to one that supports the target text before "
                "retrying verification."
            )
        if _is_auth_blocked(state):
            return None
        # Text snapshots cannot prove whether a canvas-backed timeline already
        # contains media. Give the replanner this structural fact so it does
        # not recover a failed canvas selection by inserting the asset again.
        try:
            state["timeline_media_present"] = bool(
                self.runtime.timeline_media_present()
            )
        except Exception:
            pass
        try:
            replan = self.planner.replan(
                task=task,
                completed_actions=completed_actions,
                failed_step=step,
                current_state=state,
                grounding_candidates=candidates,
                error=_format_error_for_replan(error),
            )
        except Exception as replan_error:
            self._write_replan_failure(step_index, step, candidates, error, replan_error)
            fallback = _rule_based_replan_fallback(
                task=task,
                failed_step=step,
                current_state=state,
                error=error,
            )
            if fallback is None:
                return None
            if self.output_dir:
                _write_json(
                    self.output_dir / "replans" / f"step_{step_index:02d}_rule_fallback.json",
                    {
                        "source": "rule_based_replan_fallback",
                        "llm_replan_error": f"{type(replan_error).__name__}: {replan_error}",
                        "plan": fallback.to_dict(),
                    },
                )
            print(
                f"Used rule-based replan fallback after step {step_index}. "
                f"New steps: {len(fallback.steps)}"
            )
            return fallback

        replan = _drop_leading_completed_steps(replan, completed_actions)

        if _repeats_ungrounded_failed_step(replan, failed_step=step, error=error):
            if self.output_dir:
                _write_json(
                    self.output_dir
                    / "replans"
                    / f"step_{step_index:02d}_rejected.json",
                    {
                        "reason": "repeated_ungrounded_failed_step",
                        "failed_step": step,
                        "error": f"{type(error).__name__}: {error}",
                        "plan": replan.to_dict(),
                        "next_action": "visual_fallback",
                    },
                )
            print(
                f"Rejected replan after step {step_index}: it only waits and repeats "
                "the same ungrounded target. Falling back to vision."
            )
            return None

        if _drops_named_target_entity(replan, failed_step=step):
            if self.output_dir:
                _write_json(
                    self.output_dir
                    / "replans"
                    / f"step_{step_index:02d}_rejected.json",
                    {
                        "reason": "dropped_named_target_entity",
                        "failed_step": step,
                        "error": f"{type(error).__name__}: {error}",
                        "plan": replan.to_dict(),
                        "next_action": "visual_fallback",
                    },
                )
            print(
                f"Rejected replan after step {step_index}: it dropped the named "
                "target entity from the failed step. Falling back to vision."
            )
            return None

        if self.output_dir:
            _write_json(
                self.output_dir / "replans" / f"step_{step_index:02d}.json",
                replan.to_dict(),
            )
        print(f"Replanned remaining workflow after step {step_index}. New steps: {len(replan.steps)}")
        return replan

    def _try_visual_fallback(
        self,
        *,
        task: str,
        step_index: int,
        step: dict,
        error: Exception,
        completed_actions: list[dict],
        before_state: dict,
        started: float,
    ) -> dict | None:
        if step.get("type") != "click":
            return None
        if not self.output_dir:
            return None
        fallback_dir = self.output_dir / "visual_fallbacks"
        fallback_dir.mkdir(parents=True, exist_ok=True)
        screenshot_path = fallback_dir / f"step_{step_index:02d}.png"
        visual_attempts: list[dict] = []
        mapping: dict = _empty_visual_selector_mapping("visual click was not completed")
        timings = {
            "snapshot_before_seconds": 0.0,
            "screenshot_seconds": 0.0,
            "visual_model_seconds": 0.0,
            "click_execution_seconds": 0.0,
            "snapshot_after_seconds": 0.0,
            "postcondition_seconds": 0.0,
        }
        try:
            attempt_error = f"{type(error).__name__}: {error}"
            for attempt in range(3):
                attempt_screenshot = (
                    screenshot_path
                    if attempt == 0
                    else fallback_dir / f"step_{step_index:02d}_retry_{attempt}.png"
                )
                timing_started = perf_counter()
                before_candidates = self._safe_snapshot()
                timings["snapshot_before_seconds"] += perf_counter() - timing_started
                compact_before_candidates = build_grounding_candidates(
                    {"type": "click", "target": step.get("target")},
                    before_candidates,
                )
                _write_json(
                    fallback_dir / f"step_{step_index:02d}_attempt_{attempt}_before.json",
                    {
                        "step": step,
                        "candidates": compact_before_candidates,
                    },
                )
                timing_started = perf_counter()
                self.runtime.screenshot(str(attempt_screenshot))
                timings["screenshot_seconds"] += perf_counter() - timing_started
                state = self.runtime.state()
                try:
                    timing_started = perf_counter()
                    click = self.visual_grounder.propose_click(
                        task=task,
                        failed_step=step,
                        current_state=state,
                        completed_actions=completed_actions,
                        screenshot_path=attempt_screenshot,
                        error=attempt_error,
                    )
                    timings["visual_model_seconds"] += perf_counter() - timing_started
                except Exception as proposal_error:
                    timings["visual_model_seconds"] += perf_counter() - timing_started
                    visual_attempts.append(
                        {
                            "attempt": attempt,
                            "screenshot": str(attempt_screenshot),
                            "proposal_error": (
                                f"{type(proposal_error).__name__}: {proposal_error}"
                            ),
                        }
                    )
                    attempt_error = (
                        "The previous vision response was invalid and could not be executed: "
                        f"{type(proposal_error).__name__}: {proposal_error}. Return only the "
                        "required valid JSON with both x and y coordinates."
                    )
                    continue
                timing_started = perf_counter()
                execution = self.executor.click_at_with_audit(
                    click.x,
                    click.y,
                    step.get("target") or "visual fallback",
                )
                timings["click_execution_seconds"] += perf_counter() - timing_started
                chosen = execution.used_selector
                self.runtime.wait(1)
                self._pause_after_action(step_index, step)
                after_state = self._ensure_authenticated(step_index, step)
                timing_started = perf_counter()
                after_candidates = self._safe_snapshot()
                timings["snapshot_after_seconds"] += perf_counter() - timing_started
                visual_attempts.append(
                    {
                        "attempt": attempt,
                        "screenshot": str(attempt_screenshot),
                        "position": {"x": click.x, "y": click.y},
                        "reason": click.reason,
                    }
                )
                try:
                    timing_started = perf_counter()
                    _verify_visual_action_state(step, after_state)
                    mapping = _map_visual_click_to_selector(
                        step=step,
                        x=click.x,
                        y=click.y,
                        visual_label=click.visible_label,
                        before_candidates=before_candidates,
                        after_candidates=after_candidates,
                        before_state=state,
                        after_state=after_state,
                    )
                    timings["postcondition_seconds"] += perf_counter() - timing_started
                    break
                except StateNotReachedError as state_error:
                    timings["postcondition_seconds"] += perf_counter() - timing_started
                    attempt_error = (
                        f"Previous visual click at ({click.x}, {click.y}) did not reach the "
                        f"required state: {state_error}. Choose a different visible target."
                    )
            else:
                raise StateNotReachedError(attempt_error)
        except Exception as visual_error:
            _write_json(
                fallback_dir / f"step_{step_index:02d}.json",
                {
                    "step_index": step_index,
                    "step": step,
                    "screenshot": str(screenshot_path),
                    "original_error": f"{type(error).__name__}: {error}",
                    "visual_error": f"{type(visual_error).__name__}: {visual_error}",
                    "attempts": visual_attempts,
                    "timings": _rounded_timings(timings),
                    "state": self.runtime.state() if self.runtime.page is not None else {},
                },
            )
            return None

        backfilled_selectors = list(mapping.get("selectors") or [])
        selector_metadata = list(mapping.get("selector_metadata") or [])
        action = {
            "step_index": step_index,
            "type": "click",
            "target": step.get("target"),
            "comment": step.get("comment"),
            "chosen_selector": backfilled_selectors[0] if backfilled_selectors else chosen,
            "fallback_selectors": backfilled_selectors,
            "selector_metadata": selector_metadata,
            "selector_backfilled": bool(mapping.get("trusted")),
            "selector_backfill_evidence": mapping,
            "visual_position": {"x": click.x, "y": click.y},
            "visual_reason": click.reason,
            "before_url": before_state.get("url"),
            "after_url": after_state.get("url"),
            "before_title": before_state.get("title"),
            "after_title": after_state.get("title"),
            "after_text_excerpt": after_state.get("text_excerpt"),
            "elapsed_seconds": round(perf_counter() - started, 4),
            "timings": _rounded_timings(timings),
        }
        _write_json(
            fallback_dir / f"step_{step_index:02d}.json",
            {
                "step_index": step_index,
                "step": step,
                "screenshot": str(screenshot_path),
                "original_error": f"{type(error).__name__}: {error}",
                "visual_position": action["visual_position"],
                "visual_reason": click.reason,
                "selector_backfill": mapping,
                "attempts": visual_attempts,
                "timings": action["timings"],
                "action": action,
            },
        )
        print(f"Used visual fallback for step {step_index}: click at ({click.x}, {click.y})")
        return action

    def _write_replan_failure(
        self,
        step_index: int,
        step: dict,
        candidates: list[dict],
        error: Exception,
        replan_error: Exception,
    ) -> None:
        if not self.output_dir:
            return
        _write_json(
            self.output_dir / "failures" / f"replan_failed_step_{step_index:02d}.json",
            {
                "step": step,
                "original_error": f"{type(error).__name__}: {error}",
                "replan_error": f"{type(replan_error).__name__}: {replan_error}",
                "state": self.runtime.state(),
                "grounding_candidates": candidates[:40],
            },
        )

    def _write_failure_context(
        self,
        *,
        step_index: int,
        step: dict,
        raw_candidates: list[dict],
        candidates: list[dict],
        error: Exception,
    ) -> None:
        if not self.output_dir:
            return
        failure_dir = self.output_dir / "failures"
        failure_dir.mkdir(parents=True, exist_ok=True)
        screenshot_path = failure_dir / f"step_{step_index:02d}.png"
        try:
            self.runtime.screenshot(str(screenshot_path))
        except Exception:
            screenshot_path = None
        state: dict[str, Any]
        try:
            state = self.runtime.state()
        except Exception:
            state = {}
        raw_failure_context = {
            "step_index": step_index,
            "step": step,
            "error_type": type(error).__name__,
            "error": str(error),
            "replan_reason": getattr(error, "reason", None),
            "original_error": getattr(error, "original_error", None),
            "retry_count": getattr(error, "retry_count", None),
            "expected_state": getattr(error, "expected", None),
            "state": state,
            "screenshot": str(screenshot_path) if screenshot_path else None,
            "candidate_count": len(candidates),
            "raw_candidate_count": len(raw_candidates),
            "top_candidates": candidates[:20],
        }
        _write_json(failure_dir / f"step_{step_index:02d}.json", raw_failure_context)
        normalized_context = context_from_capture_failure(raw_failure_context)
        _write_json(failure_dir / f"failure_context_step_{step_index:02d}.json", normalized_context)
        _write_json(
            failure_dir / f"classification_step_{step_index:02d}.json",
            classify_failure(normalized_context),
        )
        if not isinstance(error, AuthenticationRequired):
            _write_json(
                failure_dir / f"replan_context_step_{step_index:02d}.json",
                {
                    "reason": "plan_mismatch_or_execution_failure",
                    "original_step": step,
                    "current_state": state,
                    "completed_actions_hint": "See generation_trace.json if present.",
                    "grounding_candidates": candidates[:40],
                    "raw_candidate_count": len(raw_candidates),
                    "instruction": (
                        "Ask the planner LLM to produce replacement remaining steps from this "
                        "current page state. Do not continue the stale original plan blindly."
                    ),
                },
            )


def _build_postcondition(step: dict, candidate: dict) -> dict | None:
    if (
        step.get("type") == "input"
        and candidate.get("semantic_type") == "contenteditable"
    ):
        postcondition = {
            "type": "editable_text_equals",
            "value": str(step.get("value") or ""),
            "selectors": list(candidate.get("selector_candidates") or []),
        }
        if _contains_cjk(postcondition["value"]):
            postcondition["requires_visual_text_rendering"] = True
        return postcondition
    if step.get("type") == "set_range":
        return {
            "type": "value_equals",
            "value": str(step.get("value") or ""),
            "selectors": list(candidate.get("selector_candidates") or []),
        }
    if step.get("type") == "drag":
        return {
            "type": "element_state_changed",
            "selectors": list(candidate.get("selector_candidates") or []),
            "before": _candidate_state(candidate),
        }
    if step.get("type") != "click":
        return None
    if str(candidate.get("role") or "").lower() != "tab":
        return None
    label = (
        candidate.get("text")
        or candidate.get("accessible_name")
        or candidate.get("aria_label")
    )
    if not label:
        return None
    return {"type": "tab_selected", "label": str(label)}


def _verify_visual_action_state(step: dict, state: dict) -> None:
    intent = " ".join(
        str(step.get(key) or "") for key in ["target", "comment"]
    ).lower()
    wants_editor_properties = (
        any(marker in intent for marker in ["timeline", "时间线"])
        and any(
            marker in intent
            for marker in [
                "properties", "property", "editing", "edit", "属性", "编辑",
            ]
        )
    )
    if not wants_editor_properties:
        return
    text = str(state.get("text_excerpt") or "").lower()
    property_markers = ["trim", "speed", "opacity", "crop", "裁剪", "速度", "不透明度"]
    if sum(marker in text for marker in property_markers) >= 2:
        return
    raise StateNotReachedError(
        "the editor properties panel did not become visible after the visual click",
        expected={"type": "editor_properties_visible"},
    )


def _find_candidate_by_selectors(candidates: list[dict], selectors: list[str]) -> dict | None:
    expected = set(selectors)
    if not expected:
        return None
    for candidate in candidates:
        if expected.intersection(candidate.get("selector_candidates") or []):
            return candidate
    return None


def _should_defer_visual_text_check(
    postcondition: dict | None,
    *,
    remaining_steps: list[ActionStep],
) -> bool:
    if not postcondition or not postcondition.get("requires_visual_text_rendering"):
        return False
    for step in remaining_steps:
        target = f"{step.target or ''} {step.comment or ''}".lower()
        if step.type in {"click", "select", "set_range", "input"} and any(
            marker in target
            for marker in (
                "color", "colour", "font", "background", "stroke", "shadow",
                "text size", "font size", "文字颜色", "字体", "背景", "描边", "阴影", "字号",
            )
        ):
            return True
        if _is_text_commit_step(step.to_dict()):
            return True
    return False


def _is_text_commit_step(step: dict) -> bool:
    text = f"{step.get('target') or ''} {step.get('comment') or ''}".lower()
    if step.get("type") == "wait" and any(
        marker in text
        for marker in ("confirm", "verify", "visible", "readable", "确认", "可读", "清晰")
    ):
        return True
    return step.get("type") == "click" and any(
        marker in text
        for marker in (
            "outside text", "outside the text", "canvas outside", "outside canvas text",
            "commit", "apply text", "文字框外", "文本框外", "提交文字", "提交文本",
        )
    )


def _has_transient_text_property_overlay(state: dict) -> bool:
    """Detect property popovers that can hide the canvas during text QA.

    These are deliberately narrow, visible-state markers.  A generic dialog
    must not be dismissed because it may be an export/auth confirmation.
    """
    text = " ".join(
        str(state.get(key) or "")
        for key in ("title", "text_excerpt", "html_excerpt")
    ).lower()
    return any(
        marker in text
        for marker in (
            "choose a color",
            "choose color",
            "select a color",
            "color picker",
            "colour picker",
            "选择颜色",
            "选取颜色",
        )
    )


def _mark_deferred_visual_text_origin_passed(
    captured_actions: list[dict],
    postcondition: dict | None,
    visual_text_check: dict,
) -> None:
    if not postcondition or not postcondition.get("_defer_until_commit"):
        return
    origin = postcondition.get("_origin_step_index")
    for action in reversed(captured_actions):
        if action.get("step_index") != origin:
            continue
        action["rendered_text_readable"] = True
        action["visual_text_check"] = visual_text_check
        action["visual_text_check_deferred"] = True
        action["postcondition_passed"] = True
        return


def _candidate_state(candidate: dict) -> dict:
    return {
        "value": candidate.get("value"),
        "aria_selected": candidate.get("aria_selected"),
        "data_state": candidate.get("data_state"),
        "rect": candidate.get("rect") or {},
    }


def _candidate_state_changed(before: dict, candidate: dict) -> bool:
    after = _candidate_state(candidate)
    for key in ("value", "aria_selected", "data_state"):
        if before.get(key) != after.get(key):
            return True
    before_rect = before.get("rect") or {}
    after_rect = after.get("rect") or {}
    return any(
        abs(float(after_rect.get(key) or 0) - float(before_rect.get(key) or 0)) >= 1
        for key in ("x", "y", "width", "height")
    )


def _build_state_postcondition(
    step: dict,
    *,
    completed_actions: list[dict],
) -> dict | None:
    step_type = step.get("type")
    if step_type == "upload":
        # Browsers do not consistently expose selected file names in visible
        # text after an upload input receives a file. Verify loaded/uploaded
        # state on an explicit wait/confirm step instead.
        return None

    if step_type != "wait":
        return None
    values: list[str] = []
    target = str(step.get("target") or "")
    comment = str(step.get("comment") or "")
    lowered = f"{target} {comment}".lower()
    if any(
        marker in lowered
        for marker in [
            "export dialog", "export settings", "export popup",
            "导出设置", "导出界面", "导出弹窗", "导出流程",
        ]
    ):
        # The task and the page may use different languages. These two labels
        # are stable structural evidence that the export panel is open.
        return {"type": "visible_text_all", "values": ["export", "format"]}
    values.extend(_file_names_in_text(f"{target} {comment}"))
    last_upload = _last_uploaded_basename(completed_actions)
    last_action_type = completed_actions[-1].get("type") if completed_actions else None
    upload_subject_markers = [
        "upload", "file", "video", "timeline", "editor",
        "上传", "文件", "视频", "素材", "时间线", "编辑器",
    ]
    state_transition_markers = [
        "load", "appear", "ready", "visible", "process",
        "导入", "加载", "出现", "就绪", "可见", "处理",
    ]
    if (
        last_upload
        and any(marker in lowered for marker in upload_subject_markers)
        and any(marker in lowered for marker in state_transition_markers)
    ):
        if any(marker in lowered for marker in ["timeline", "时间线"]):
            # The media library may already show the uploaded filename while
            # the timeline is still empty. Require editor controls instead.
            return {"type": "timeline_media_present"}
        else:
            values.append(last_upload)
            stem = Path(last_upload).stem
            if stem and stem.lower() != last_upload.lower():
                values.append(stem)
    if last_action_type == "double_click" and any(
        marker in lowered
        for marker in ["timeline", "editor", "时间线", "编辑器", "素材"]
    ):
        # Media libraries commonly use a double click to insert an asset.  A
        # successful insertion exposes at least one timeline/editor control;
        # merely selecting the library card must not satisfy the wait.
        return {"type": "timeline_media_present"}
    # A comment explains intent to the executor; it is not necessarily literal
    # UI copy. Only an explicit wait target may become a visible-text assertion.
    cleaned = _visible_text_hint(target)
    if cleaned:
        values.append(cleaned)
    values = _dedupe_strings(values)
    if not values:
        return None
    return {"type": "visible_text_contains", "values": values}


def _last_uploaded_basename(actions: list[dict]) -> str | None:
    # An upload assertion belongs only to the immediately following confirm/wait
    # step.  A later navigation or edit action starts a new state transition and
    # must not keep requiring a stale file name forever.
    if not actions or actions[-1].get("type") != "upload":
        return None
    path = str(actions[-1].get("path") or "")
    if path:
        return Path(path).name
    return None


def _file_names_in_text(text: str) -> list[str]:
    return re.findall(
        r"(?<![\w.-])[^\s，,。；;:：/\\]+\.(?:mp4|mov|webm|avi|mkv|jpg|jpeg|png|gif|txt|pdf)(?![\w.-])",
        str(text),
        flags=re.IGNORECASE,
    )


def _visible_text_hint(text: str) -> str | None:
    cleaned = " ".join(str(text or "").split()).strip()
    if not cleaned:
        return None
    lowered = cleaned.lower()
    ignored_phrases = [
        "wait",
        "wait for",
        "appears",
        "appear",
        "loads",
        "load",
        "ready",
        "visible",
        "确认",
        "等待",
        "出现",
        "加载",
    ]
    for phrase in ignored_phrases:
        lowered = lowered.replace(phrase, " ")
    cleaned = " ".join(lowered.split()).strip()
    if not cleaned or len(cleaned) < 3:
        return None
    generic = {
        "video clip in timeline",
        "video clip",
        "timeline panel",
        "editor page",
        "editor",
        "page",
        "timeline",
        "after replan",
    }
    if cleaned in generic:
        return None
    allowed_short_ui_text = {
        "export",
        "save",
        "download",
        "done",
        "complete",
        "completed",
        "ready",
        "trim",
        "reset",
    }
    if cleaned not in allowed_short_ui_text and len(cleaned.split()) > 1:
        return None
    return cleaned


def _dedupe_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        normalized = " ".join(str(value or "").strip().split())
        if not normalized:
            continue
        key = normalized.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(normalized)
    return result


def _trace_entry(
    step_index: int,
    step: dict,
    grounding: dict | None,
    action: dict,
    started: float,
) -> dict:
    return {
        "step_index": step_index,
        "step": step,
        "grounding": grounding,
        "captured_action": action,
        "elapsed_seconds": round(perf_counter() - started, 4),
    }


def _write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _selector_indexes(selector_metadata: list[dict]) -> dict[str, int]:
    return {
        item["selector"]: int(item.get("index", 0))
        for item in selector_metadata
        if item.get("selector") and item.get("match_count", 1) > 1
    }


def _selector_metadata_for(selector_metadata: list[dict], selector: str) -> dict:
    for item in selector_metadata:
        if item.get("selector") == selector:
            return item
    return {}


def _drop_leading_completed_steps(
    replan: ActionPlan, completed_actions: list[dict]
) -> ActionPlan:
    completed_click_targets = {
        _semantic_target(action.get("target"))
        for action in completed_actions
        if action.get("type") == "click" and action.get("target")
    }
    steps = list(replan.steps)
    while (
        steps
        and steps[0].type == "click"
        and _semantic_target(steps[0].target) in completed_click_targets
    ):
        steps.pop(0)
    return ActionPlan(
        task=replan.task,
        steps=steps,
        success_assertions=replan.success_assertions,
    )


def _repeats_ungrounded_failed_step(
    replan: ActionPlan,
    *,
    failed_step: dict,
    error: Exception,
) -> bool:
    if "no dom candidate matched target" not in str(error).lower():
        return False
    first_action = next((step for step in replan.steps if step.type != "wait"), None)
    if first_action is None:
        return False
    return (
        first_action.type == failed_step.get("type")
        and _semantic_target(first_action.target)
        == _semantic_target(failed_step.get("target"))
    )


def _drops_named_target_entity(replan: ActionPlan, *, failed_step: dict) -> bool:
    if failed_step.get("type") != "click":
        return False
    entity = _named_entity_phrase(str(failed_step.get("target") or ""))
    if not entity:
        return False
    first_action = next((step for step in replan.steps if step.type != "wait"), None)
    if first_action is None or first_action.type != "click":
        return False
    return entity.lower() not in _semantic_target(first_action.target)


def _named_entity_phrase(text: str) -> str | None:
    ignored = {
        "Add Text",
        "Create Project",
        "Text Tool",
        "Export",
        "Sample Text",
    }
    for match in re.finditer(r"\b([A-Z][a-z0-9]+(?:\s+[A-Z][a-z0-9]+)+)\b", text):
        phrase = " ".join(match.group(1).split())
        if phrase not in ignored:
            return phrase
    return None


def _visual_fallback_step(task: str, step: dict, error: Exception) -> dict:
    if step.get("type") != "click":
        return step
    if getattr(error, "reason", None) != "ambiguous_target":
        return step
    target = str(step.get("target") or "")
    if "add text" not in target.lower():
        return step
    entity = _named_entity_phrase(task)
    if not entity or entity.lower() in target.lower():
        return step
    enriched = dict(step)
    enriched["target"] = f"{entity} text style"
    enriched["comment"] = (
        f"{step.get('comment') or ''} Visual fallback target enriched from task "
        f"to preserve the named style {entity!r}."
    ).strip()
    return enriched


def _format_error_for_replan(error: Exception) -> str:
    message = f"{type(error).__name__}: {error}"
    visual_text_check = _visual_text_check_from_error(error)
    if not visual_text_check:
        return message
    return (
        f"{message}\n"
        f"visual_text_check={json.dumps(visual_text_check, ensure_ascii=False)}\n"
        "The DOM text may already match, but screenshot-level rendered text is not readable. "
        "Recovery must address visual rendering, usually by changing the selected font/style "
        "before verifying or re-entering the text."
    )


def _visual_text_check_from_error(error: Exception) -> dict | None:
    expected = getattr(error, "expected", None)
    if not isinstance(expected, dict):
        return None
    visual_text_check = expected.get("visual_text_check")
    if not isinstance(visual_text_check, dict):
        return None
    return visual_text_check


def _visual_text_postcondition_from_error(error: Exception) -> dict | None:
    expected = getattr(error, "expected", None)
    if not isinstance(expected, dict):
        return None
    if not isinstance(expected.get("visual_text_check"), dict):
        return None
    postcondition = {
        key: value
        for key, value in expected.items()
        if key != "visual_text_check"
    }
    if not postcondition.get("requires_visual_text_rendering"):
        postcondition["requires_visual_text_rendering"] = True
    return postcondition


def _rule_based_replan_fallback(
    *,
    task: str,
    failed_step: dict,
    current_state: dict,
    error: Exception,
) -> ActionPlan | None:
    if failed_step.get("type") != "goto":
        return None
    if getattr(error, "reason", None) != "state_not_reached":
        return None
    failed_url = str(failed_step.get("url") or current_state.get("url") or "")
    base_url = _origin_url(failed_url)
    if not base_url or base_url == failed_url.rstrip("/") + "/":
        return None

    steps = [ActionStep("goto", url=base_url, comment="Rule fallback: open site home after unreachable route")]
    feature_target = _feature_target_from_url_path(failed_url)
    if feature_target:
        steps.append(
            ActionStep(
                "click",
                target=feature_target,
                comment="Rule fallback: open feature from the site home page",
            )
        )
    upload_path = _first_file_path_from_task(task)
    if upload_path:
        steps.append(
            ActionStep(
                "upload",
                target="file upload area",
                path=upload_path,
                comment="Rule fallback: upload requested local file",
            )
        )
        steps.append(
            ActionStep(
                "wait",
                target="uploaded file appears in editor",
                value="5",
                comment="Rule fallback: wait until uploaded file is visible in the editor",
            )
        )
    return ActionPlan(task=task, steps=steps, success_assertions=[])


def _origin_url(url: str) -> str | None:
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return None
    return urlunparse((parsed.scheme, parsed.netloc, "/", "", "", ""))


def _feature_target_from_url_path(url: str) -> str | None:
    path = urlparse(url).path.strip("/")
    if not path:
        return None
    segment = path.split("/")[0]
    words = [word for word in segment.replace("_", "-").split("-") if word]
    if not words:
        return None
    return " ".join(word.capitalize() for word in words)


def _first_file_path_from_task(task: str) -> str | None:
    pattern = re.compile(
        r"(?P<path>(?:/|~/?|[A-Za-z]:[\\/])[^\s，,。.;；:：\"'“”‘’()\[\]{}<>]+"
        r"\.(?:mp4|mov|webm|avi|mkv|jpg|jpeg|png|gif|txt|pdf))",
        re.IGNORECASE,
    )
    match = pattern.search(str(task))
    if match:
        return match.group("path")
    return None


def _contains_cjk(text: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", text))


def _empty_visual_selector_mapping(reason: str) -> dict:
    return {
        "trusted": False,
        "confidence": 0.0,
        "reason": reason,
        "candidate_id": None,
        "candidate_rect": None,
        "selectors": [],
        "selector_metadata": [],
        "state_changed": False,
    }


def _rounded_timings(timings: dict[str, float]) -> dict[str, float]:
    return {key: round(float(value), 4) for key, value in timings.items()}


def _map_visual_click_to_selector(
    *,
    step: dict,
    x: int,
    y: int,
    visual_label: str | None = None,
    before_candidates: list[dict],
    after_candidates: list[dict],
    before_state: dict,
    after_state: dict,
) -> dict:
    """Backfill a visual click only when DOM and outcome evidence both agree."""
    state_changed, change_evidence = _visual_click_change_evidence(
        before_state=before_state,
        after_state=after_state,
        before_candidates=before_candidates,
        after_candidates=after_candidates,
    )
    containing = [
        candidate
        for candidate in before_candidates
        if _candidate_contains_point(candidate, x=x, y=y)
    ]
    if not containing:
        result = _empty_visual_selector_mapping("no visible DOM candidate contains the visual coordinate")
        result["state_changed"] = state_changed
        result["change_evidence"] = change_evidence
        return result

    ranked: list[tuple[tuple, dict, float, list[str]]] = []
    for candidate in containing:
        semantic_score, semantic_evidence = _visual_candidate_semantic_score(step, candidate)
        visual_semantic_score = 0.0
        if visual_label:
            visual_semantic_score, visual_evidence = _visual_candidate_semantic_score(
                step,
                {"text": visual_label},
            )
            if visual_semantic_score > semantic_score:
                semantic_score = visual_semantic_score
                semantic_evidence = [f"vision-confirmed-label:{item}" for item in visual_evidence]
        clickable = "click" in (candidate.get("action_allowed") or [])
        genuine_clickable = (
            str(candidate.get("tag") or "").lower() in {"button", "a", "label"}
            or str(candidate.get("role") or "").lower() in {"button", "link", "tab", "menuitem"}
            or candidate.get("semantic_type")
            in {"button", "link", "clickable_item", "library_button", "result_item"}
        )
        rect = candidate.get("rect") or {}
        area = max(1.0, float(rect.get("width") or 0) * float(rect.get("height") or 0))
        selectors = _stable_visual_selectors(candidate)
        ranked.append(
            (
                (
                    int(clickable),
                    int(genuine_clickable),
                    semantic_score,
                    int(bool(selectors)),
                    -area,
                ),
                candidate,
                semantic_score,
                semantic_evidence,
            )
        )
    ranked.sort(key=lambda item: item[0], reverse=True)
    _, candidate, semantic_score, semantic_evidence = ranked[0]
    selectors = _stable_visual_selectors(candidate)
    clickable = "click" in (candidate.get("action_allowed") or [])

    reasons: list[str] = []
    if not clickable:
        reasons.append("coordinate candidate is not click-capable")
    if semantic_score < 0.72:
        reasons.append("candidate semantics do not match the requested target")
    if not selectors:
        reasons.append("candidate has no stable selector")
    if not state_changed:
        reasons.append("click produced no observable state or candidate change")

    trusted = not reasons
    confidence = min(
        0.99,
        0.45 + 0.30 * semantic_score + (0.12 if clickable else 0) + (0.12 if state_changed else 0),
    ) if trusted else min(0.69, 0.35 * semantic_score + (0.15 if clickable else 0))
    metadata_by_selector = {
        item.get("selector"): item
        for item in candidate.get("selector_metadata") or []
        if item.get("selector")
    }
    return {
        "trusted": trusted,
        "confidence": round(confidence, 3),
        "reason": "selector mapping passed DOM, semantic, and state-change checks"
        if trusted
        else "; ".join(reasons),
        "candidate_id": candidate.get("candidate_id"),
        "candidate_rect": candidate.get("rect"),
        "candidate_tag": candidate.get("tag"),
        "candidate_text": candidate.get("text"),
        "semantic_score": round(semantic_score, 3),
        "semantic_evidence": semantic_evidence,
        "selectors": selectors if trusted else [],
        "selector_metadata": [
            metadata_by_selector.get(
                selector,
                {"selector": selector, "index": 0, "match_count": 1, "unique": True},
            )
            for selector in selectors
        ] if trusted else [],
        "state_changed": state_changed,
        "change_evidence": change_evidence,
        "containing_candidate_ids": [item.get("candidate_id") for item in containing],
    }


def _candidate_contains_point(candidate: dict, *, x: int, y: int) -> bool:
    if candidate.get("is_visible") is False:
        return False
    rect = candidate.get("rect") or {}
    left = float(rect.get("x") or 0)
    top = float(rect.get("y") or 0)
    width = float(rect.get("width") or 0)
    height = float(rect.get("height") or 0)
    return width > 0 and height > 0 and left <= x <= left + width and top <= y <= top + height


def _visual_candidate_semantic_score(step: dict, candidate: dict) -> tuple[float, list[str]]:
    target = _semantic_target(step.get("target"))
    labels = [
        _semantic_target(candidate.get(key))
        for key in ("text", "accessible_name", "aria_label", "label_text")
        if candidate.get(key)
    ]
    if not target or not labels:
        return 0.0, labels
    target_tokens = set(re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]", target))
    best = 0.0
    for label in labels:
        if label == target:
            best = max(best, 1.0)
            continue
        if label in target and len(label) >= 3:
            best = max(best, 0.92)
            continue
        if target in label:
            # Do not bind a short named control to a longer sibling such as
            # "Text" -> "Text to speech".
            extra = set(re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]", label)) - target_tokens
            best = max(best, 0.68 if extra else 0.9)
            continue
        label_tokens = set(re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]", label))
        union = target_tokens | label_tokens
        if union:
            best = max(best, len(target_tokens & label_tokens) / len(union))
    return best, labels


def _stable_visual_selectors(candidate: dict) -> list[str]:
    metadata = candidate.get("selector_metadata") or []
    metadata_by_selector = {item.get("selector"): item for item in metadata}
    stable: list[str] = []
    for selector in candidate.get("selector_candidates") or []:
        lowered = str(selector).lower()
        if selector.startswith("xpath:/html/"):
            continue
        if ":nth-of-type(" in lowered and not re.search(
            r"(?:#[a-z0-9_-]+|\.[a-z][a-z0-9_-]{2,}|\[data-[^\]]+\])",
            lowered,
        ):
            continue
        item = metadata_by_selector.get(selector) or {}
        match_count = int(item.get("match_count", 1) or 1)
        if match_count > 1 and item.get("index") is None:
            continue
        stable.append(selector)
    return stable


def _visual_click_change_evidence(
    *,
    before_state: dict,
    after_state: dict,
    before_candidates: list[dict],
    after_candidates: list[dict],
) -> tuple[bool, list[str]]:
    evidence: list[str] = []
    for key in ("url", "title", "text_excerpt"):
        if str(before_state.get(key) or "") != str(after_state.get(key) or ""):
            evidence.append(f"{key}_changed")
    before_fingerprint = _candidate_set_fingerprint(before_candidates)
    after_fingerprint = _candidate_set_fingerprint(after_candidates)
    if before_fingerprint != after_fingerprint:
        evidence.append("candidate_set_changed")
    return bool(evidence), evidence


def _candidate_set_fingerprint(candidates: list[dict]) -> tuple:
    return tuple(sorted(
        (
            str(candidate.get("candidate_id") or ""),
            str(candidate.get("text") or ""),
            str(candidate.get("aria_selected") or ""),
            str(candidate.get("data_state") or ""),
            tuple(candidate.get("selector_candidates") or []),
        )
        for candidate in candidates
        if candidate.get("is_visible") is not False
    ))


def _semantic_target(value: object) -> str:
    return " ".join(str(value or "").lower().split())


def _should_use_visual_fallback(
    *,
    error: Exception,
    step: dict,
    state: dict,
    replan_count: int,
) -> bool:
    if step.get("type") != "click":
        return False
    error_text = str(error).lower()
    target = _semantic_target(step.get("target"))
    visible_text = _semantic_target(state.get("text_excerpt"))
    if (
        "no dom candidate matched target" in error_text
        and _is_named_color_swatch_target(target)
        and any(marker in visible_text for marker in ("choose a color", "color picker", "选择颜色"))
    ):
        # Painted palette swatches frequently have no label or stable DOM
        # identity. Once the picker is visibly open, replanning cannot improve
        # the grounding evidence; vision is the correct final click layer.
        return True
    if replan_count < 1:
        return False
    if getattr(error, "reason", None) == "ambiguous_target":
        return True
    if "no dom candidate matched target" not in error_text:
        return False
    if not target:
        return False
    return target in visible_text


def _is_named_color_swatch_target(target: str) -> bool:
    return any(
        marker in target
        for marker in (
            "black", "white", "red", "green", "blue", "#000", "#fff",
            "黑色", "白色", "红色", "绿色", "蓝色", "色块",
        )
    )


def _has_actionable_candidates(candidates: list[dict]) -> bool:
    for candidate in candidates:
        rect = candidate.get("rect") or {}
        if rect.get("width", 0) <= 0 or rect.get("height", 0) <= 0:
            continue
        allowed = candidate.get("action_allowed") or []
        if any(action in allowed for action in ["click", "input", "select", "upload"]):
            return True
    return False


def _is_auth_blocked(state: dict) -> bool:
    url = str(state.get("url") or "").lower()
    title = str(state.get("title") or "").lower()
    text = str(state.get("text_excerpt") or state.get("html_excerpt") or "").lower()
    auth_markers = [
        "/login",
        "appleid.apple.com/auth",
        "accounts.google.com",
        "oauth",
        "signin",
        "sign-in",
    ]
    if any(marker in url for marker in auth_markers):
        return True
    if "login" in title or "sign in" in title or "登录" in title:
        return True
    signed_out_markers = [
        "sign in with email",
        "sign in with google",
        "continue with google",
        "continue with apple",
        "continue with email",
        "forgot password",
        "登录",
    ]
    return any(marker in text for marker in signed_out_markers)


def _is_security_challenge(state: dict) -> bool:
    text = str(state.get("text_excerpt") or state.get("html_excerpt") or "").lower()
    markers = [
        "performing security verification",
        "verify you are human",
        "checking your browser",
        "cloudflare",
    ]
    return any(marker in text for marker in markers)

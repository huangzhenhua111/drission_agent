from __future__ import annotations

import json
from pathlib import Path
from time import perf_counter
from typing import Any

from app.generation.candidate_compactor import build_grounding_candidates
from app.generation.exceptions import AuthenticationRequired
from app.generation.planner import ActionPlan
from app.generation.planner import Planner
from app.generation.selector_grounder import SelectorGrounder
from app.generation.visual_fallback import VisualFallbackGrounder
from app.runtime.drission_runtime import DrissionRuntime


class CaptureRunner:
    def __init__(
        self,
        *,
        runtime: DrissionRuntime | None = None,
        grounder: SelectorGrounder | None = None,
        planner: Planner | None = None,
        visual_grounder: VisualFallbackGrounder | None = None,
        output_dir: Path | None = None,
        wait_for_login: bool = False,
        login_timeout_seconds: int = 300,
        max_replans: int = 1,
        action_delay_seconds: float = 0,
    ) -> None:
        self.runtime = runtime or DrissionRuntime()
        self.grounder = grounder or SelectorGrounder()
        self.planner = planner
        self.visual_grounder = visual_grounder or VisualFallbackGrounder()
        self.output_dir = output_dir
        self.wait_for_login = wait_for_login
        self.login_timeout_seconds = login_timeout_seconds
        self.max_replans = max_replans
        self.action_delay_seconds = max(0.0, float(action_delay_seconds))

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
                    captured_actions.append(action)
                    trace.append(_trace_entry(step_index, step_dict, None, action, started))
                    cursor += 1
                    continue

                if step.type == "wait":
                    seconds = float(step.value or 1)
                    self.runtime.wait(seconds)
                    after_state = self._ensure_authenticated(step_index, step_dict)
                    after_state = self._pause_after_action(step_index, step_dict, after_state)
                    action = {
                        "step_index": step_index,
                        "type": step.type,
                        "target": step.target,
                        "comment": step.comment,
                        "seconds": seconds,
                        "chosen_selector": None,
                        "fallback_selectors": [],
                        "before_url": before_state.get("url"),
                        "after_url": after_state.get("url"),
                        "before_title": before_state.get("title"),
                        "after_title": after_state.get("title"),
                        "after_text_excerpt": after_state.get("text_excerpt"),
                    }
                    captured_actions.append(action)
                    trace.append(_trace_entry(step_index, step_dict, None, action, started))
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
                    _write_json(
                        output_dir / "dom_snapshots" / f"raw_step_{step_index:02d}.json",
                        {"step": step_dict, "candidates": raw_candidates},
                    )

                try:
                    grounding = self.grounder.ground(
                        step=step_dict,
                        candidates=candidates,
                        context=context,
                    )
                    selectors = grounding["selectors"]
                    selector_metadata = grounding.get("selector_metadata") or []
                    selector_indexes = _selector_indexes(selector_metadata)
                    chosen_selector = self._execute_step(step_dict, selectors, selector_indexes)
                    chosen_metadata = _selector_metadata_for(
                        selector_metadata, chosen_selector
                    )
                    self._pause_after_action(step_index, step_dict)
                    after_state = self._ensure_authenticated(step_index, step_dict)
                    postcondition = _build_postcondition(
                        step_dict, grounding.get("candidate") or {}
                    )
                    self._verify_postcondition(postcondition)
                except Exception as exc:
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
                    visual_action = self._try_visual_fallback(
                        task=task,
                        step_index=step_index,
                        step=step_dict,
                        error=exc,
                        completed_actions=captured_actions,
                        before_state=before_state,
                        started=started,
                    )
                    if visual_action is not None:
                        captured_actions.append(visual_action)
                        trace.append(_trace_entry(step_index, step_dict, None, visual_action, started))
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
                    "context_text": grounding.get("candidate", {}).get("context_text"),
                    "result_rank": grounding.get("candidate", {}).get("result_rank"),
                    "related_item_rank": grounding.get("candidate", {}).get("related_item_rank"),
                    "related_item_context": grounding.get("candidate", {}).get("related_item_context"),
                    "upload_label": grounding.get("candidate", {}).get("upload_label"),
                    "upload_kind": grounding.get("candidate", {}).get("upload_kind"),
                    "value": step.value,
                    "path": step.path,
                    "select_by": step.select_by,
                    "before_url": before_state.get("url"),
                    "after_url": after_state.get("url"),
                    "before_title": before_state.get("title"),
                    "after_title": after_state.get("title"),
                    "after_text_excerpt": after_state.get("text_excerpt"),
                }
                captured_actions.append(action)
                trace.append(_trace_entry(step_index, step_dict, grounding, action, started))
                cursor += 1

            if output_dir:
                _write_json(output_dir / "captured_actions.json", captured_actions)
                _write_json(output_dir / "generation_trace.json", trace)
            return captured_actions
        finally:
            self.runtime.close()

    def _execute_step(
        self,
        step: dict,
        selectors: list[str],
        selector_indexes: dict[str, int],
    ) -> str:
        step_type = step["type"]
        target = step.get("target") or step_type
        if step_type == "click":
            return self.runtime.click(selectors, target, selector_indexes=selector_indexes)
        if step_type == "input":
            return self.runtime.input(
                selectors,
                step.get("value", ""),
                target,
                selector_indexes=selector_indexes,
            )
        if step_type == "select":
            return self.runtime.select(
                selectors,
                step.get("value", ""),
                step.get("select_by") or "text",
                target,
                selector_indexes=selector_indexes,
            )
        if step_type == "upload":
            return self.runtime.upload(
                selectors,
                step.get("path", ""),
                target,
                selector_indexes=selector_indexes,
            )
        raise ValueError(f"Unsupported capture step type: {step_type}")

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
            self.runtime.screenshot(
                str(self.output_dir / "screenshots" / f"after_step_{step_index:02d}.png")
            )
        try:
            return self.runtime.state()
        except Exception:
            return current_state or {}

    def _verify_postcondition(self, postcondition: dict | None) -> None:
        if not postcondition or postcondition.get("type") != "tab_selected":
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

    def _snapshot_with_retry(self) -> list[dict]:
        candidates = self.runtime.snapshot()
        for _ in range(6):
            if _has_actionable_candidates(candidates):
                return candidates
            self.runtime.wait(3)
            candidates = self.runtime.snapshot()
        return candidates

    def _ensure_authenticated(self, step_index: int, step: dict) -> dict:
        state = self.runtime.state()
        if not _is_auth_blocked(state):
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

    def _wait_for_login_state(self, *, step_index: int, step: dict) -> dict:
        print(
            "Authentication required. Finish login in the opened browser window; "
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
            if _is_auth_blocked(state):
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
        if _is_auth_blocked(state):
            return None
        try:
            replan = self.planner.replan(
                task=task,
                completed_actions=completed_actions,
                failed_step=step,
                current_state=state,
                grounding_candidates=candidates,
                error=f"{type(error).__name__}: {error}",
            )
        except Exception as replan_error:
            self._write_replan_failure(step_index, step, candidates, error, replan_error)
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
        if not self.output_dir:
            return None
        fallback_dir = self.output_dir / "visual_fallbacks"
        fallback_dir.mkdir(parents=True, exist_ok=True)
        screenshot_path = fallback_dir / f"step_{step_index:02d}.png"
        try:
            self.runtime.screenshot(str(screenshot_path))
            state = self.runtime.state()
            click = self.visual_grounder.propose_click(
                task=task,
                failed_step=step,
                current_state=state,
                completed_actions=completed_actions,
                screenshot_path=screenshot_path,
                error=f"{type(error).__name__}: {error}",
            )
            chosen = self.runtime.click_at(click.x, click.y, step.get("target") or "visual fallback")
            self.runtime.wait(1)
            self._pause_after_action(step_index, step)
            after_state = self._ensure_authenticated(step_index, step)
        except Exception as visual_error:
            _write_json(
                fallback_dir / f"step_{step_index:02d}.json",
                {
                    "step_index": step_index,
                    "step": step,
                    "screenshot": str(screenshot_path),
                    "original_error": f"{type(error).__name__}: {error}",
                    "visual_error": f"{type(visual_error).__name__}: {visual_error}",
                    "state": self.runtime.state() if self.runtime.page is not None else {},
                },
            )
            return None

        action = {
            "step_index": step_index,
            "type": "click",
            "target": step.get("target"),
            "comment": step.get("comment"),
            "chosen_selector": chosen,
            "fallback_selectors": [],
            "selector_metadata": [],
            "visual_position": {"x": click.x, "y": click.y},
            "visual_reason": click.reason,
            "before_url": before_state.get("url"),
            "after_url": after_state.get("url"),
            "before_title": before_state.get("title"),
            "after_title": after_state.get("title"),
            "after_text_excerpt": after_state.get("text_excerpt"),
            "elapsed_seconds": round(perf_counter() - started, 4),
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
        _write_json(
            failure_dir / f"step_{step_index:02d}.json",
            {
                "step_index": step_index,
                "step": step,
                "error_type": type(error).__name__,
                "error": str(error),
                "state": state,
                "screenshot": str(screenshot_path) if screenshot_path else None,
                "candidate_count": len(candidates),
                "raw_candidate_count": len(raw_candidates),
                "top_candidates": candidates[:20],
            },
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
        "sign in",
        "log in",
        "login",
        "sign in with email",
        "sign in with google",
        "登录",
    ]
    return any(marker in text for marker in signed_out_markers)

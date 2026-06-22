from __future__ import annotations

from copy import deepcopy

from app.debug.failure_context import classify_failure


class ScriptFixer:
    def build_fix_request(self, failure_context: dict, classification: dict | None = None) -> dict:
        classification = classification or classify_failure(failure_context)
        category = str(classification.get("category") or "")
        return {
            "schema_version": 1,
            "failure_context": deepcopy(failure_context),
            "classification": deepcopy(classification),
            "category": category,
            "recommended_strategy": classification.get("recommended_strategy"),
            "recoverable": bool(classification.get("recoverable")),
            "requires_llm": bool(classification.get("llm_escalation_allowed")),
            "patch_constraints": {
                "preserve_postconditions": True,
                "do_not_delete_assertions": True,
                "limit_scope_to_failed_action": category
                not in {"auth_required", "security_challenge", "task_unsatisfiable", "budget_exhausted"},
            },
            "allowed_patch_scope": _allowed_patch_scope(category),
        }

    def fix(self, failure_context: dict, classification: dict | None = None) -> str:
        request = self.build_fix_request(failure_context, classification)
        category = request["category"] or "unknown"
        raise NotImplementedError(
            f"Script fixer patch generation lands in the next phase; received classified failure {category!r}."
        )


def _allowed_patch_scope(category: str) -> list[str]:
    scopes = {
        "selector_miss": ["failed_action_selectors", "selector_wait"],
        "selector_not_unique": ["failed_action_selectors"],
        "wrong_target": ["failed_action_target", "failed_action_selectors", "postcondition_wait"],
        "ambiguous_target": ["failed_action_target", "planner_hint"],
        "postcondition_failed": ["state_wait", "failed_action_postcondition"],
        "state_unchanged": ["failed_action_retry", "state_wait"],
        "network_or_load_delay": ["targeted_wait", "timeout"],
        "visual_mapping_untrusted": ["visual_grounding_request", "selector_backfill_evidence"],
        "runtime_exception": ["failed_action_runtime_guard"],
        "auth_required": ["manual_login_resume"],
        "security_challenge": ["manual_verification_resume"],
        "task_unsatisfiable": ["no_patch"],
        "budget_exhausted": ["no_patch"],
    }
    return scopes.get(category, ["manual_review"])

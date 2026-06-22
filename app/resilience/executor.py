from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable


class ReplanRequired(RuntimeError):
    """Raised when the current DOM/state is not reliable enough to continue."""

    def __init__(
        self,
        message: str,
        *,
        reason: str,
        original_error: str | None = None,
        retry_count: int = 0,
    ) -> None:
        super().__init__(message)
        self.reason = reason
        self.original_error = original_error
        self.retry_count = retry_count


@dataclass(frozen=True)
class ExecutionResult:
    used_selector: str
    fallback_level: int
    retry_count: int = 0
    refreshed_snapshot: bool = False
    notes: list[str] = field(default_factory=list)


class ResilientActionExecutor:
    """Execute browser actions through one retry/fallback/replan decision point.

    BrowserRuntime/DrissionRuntime remains the low-level primitive provider. This
    executor is the boundary used by higher-level workflows so direct DOM action
    failures are classified consistently before replan or visual fallback.
    """

    def __init__(
        self,
        runtime,
        *,
        snapshot_fn: Callable[[], list[dict]] | None = None,
        wait_seconds: float = 1.0,
    ) -> None:
        self.runtime = runtime
        self.snapshot_fn = snapshot_fn
        self.wait_seconds = wait_seconds

    def execute(
        self,
        step: dict,
        selectors: list[str],
        *,
        selector_indexes: dict[str, int] | None = None,
    ) -> ExecutionResult:
        step_type = step.get("type")
        target = step.get("target") or step_type or "action"
        if step_type == "click":
            return self.click_with_fallback(
                selectors,
                target,
                selector_indexes=selector_indexes,
            )
        if step_type == "double_click":
            return self.double_click_with_fallback(
                selectors,
                target,
                selector_indexes=selector_indexes,
            )
        if step_type == "input":
            return self.input_with_retry(
                selectors,
                str(step.get("value") or ""),
                target,
                selector_indexes=selector_indexes,
            )
        if step_type == "select":
            return self.select_with_retry(
                selectors,
                str(step.get("value") or ""),
                str(step.get("select_by") or "text"),
                target,
                selector_indexes=selector_indexes,
            )
        if step_type == "upload":
            return self.upload_with_retry(
                selectors,
                str(step.get("path") or ""),
                target,
                selector_indexes=selector_indexes,
            )
        if step_type == "set_range":
            return self.set_range_with_retry(
                selectors,
                str(step.get("value") or ""),
                target,
                selector_indexes=selector_indexes,
            )
        if step_type == "set_timecode":
            return self.set_timecode_with_retry(
                selectors,
                str(step.get("value") or ""),
                target,
                selector_indexes=selector_indexes,
            )
        if step_type == "drag":
            return self.drag_with_retry(
                selectors,
                float(step.get("delta_x") or 0),
                float(step.get("delta_y") or 0),
                float(step.get("duration") or 0.5),
                target,
                selector_indexes=selector_indexes,
            )
        if step_type == "press_key":
            return self.press_key_with_retry(str(step.get("value") or ""), target)
        raise ValueError(f"Unsupported resilient action type: {step_type}")

    def click_with_fallback(
        self,
        selectors: list[str],
        target: str,
        *,
        selector_indexes: dict[str, int] | None = None,
    ) -> ExecutionResult:
        return self._execute_with_retry(
            action="click",
            selectors=selectors,
            target=target,
            call=lambda: self.runtime.click(
                selectors,
                target,
                selector_indexes=selector_indexes,
            ),
        )

    def double_click_with_fallback(
        self,
        selectors: list[str],
        target: str,
        *,
        selector_indexes: dict[str, int] | None = None,
    ) -> ExecutionResult:
        return self._execute_with_retry(
            action="double_click",
            selectors=selectors,
            target=target,
            call=lambda: self.runtime.double_click(
                selectors,
                target,
                selector_indexes=selector_indexes,
            ),
        )

    def input_with_retry(
        self,
        selectors: list[str],
        value: str,
        target: str,
        *,
        selector_indexes: dict[str, int] | None = None,
    ) -> ExecutionResult:
        return self._execute_with_retry(
            action="input",
            selectors=selectors,
            target=target,
            call=lambda: self.runtime.input(
                selectors,
                value,
                target,
                selector_indexes=selector_indexes,
            ),
        )

    def select_with_retry(
        self,
        selectors: list[str],
        value: str,
        by: str,
        target: str,
        *,
        selector_indexes: dict[str, int] | None = None,
    ) -> ExecutionResult:
        return self._execute_with_retry(
            action="select",
            selectors=selectors,
            target=target,
            call=lambda: self.runtime.select(
                selectors,
                value,
                by,
                target,
                selector_indexes=selector_indexes,
            ),
        )

    def upload_with_retry(
        self,
        selectors: list[str],
        path: str,
        target: str,
        *,
        selector_indexes: dict[str, int] | None = None,
    ) -> ExecutionResult:
        return self._execute_with_retry(
            action="upload",
            selectors=selectors,
            target=target,
            call=lambda: self.runtime.upload(
                selectors,
                path,
                target,
                selector_indexes=selector_indexes,
            ),
        )

    def click_at_with_audit(self, x: int, y: int, target: str) -> ExecutionResult:
        used_selector = self.runtime.click_at(x, y, target)
        return ExecutionResult(
            used_selector=used_selector,
            fallback_level=4,
            notes=["visual_fallback"],
        )

    def set_range_with_retry(
        self,
        selectors: list[str],
        value: str,
        target: str,
        *,
        selector_indexes: dict[str, int] | None = None,
    ) -> ExecutionResult:
        return self._execute_with_retry(
            action="set_range",
            selectors=selectors,
            target=target,
            call=lambda: self.runtime.set_range(
                selectors, value, target, selector_indexes=selector_indexes
            ),
        )

    def set_timecode_with_retry(
        self,
        selectors: list[str],
        value: str,
        target: str,
        *,
        selector_indexes: dict[str, int] | None = None,
    ) -> ExecutionResult:
        return self._execute_with_retry(
            action="set_timecode",
            selectors=selectors,
            target=target,
            call=lambda: self.runtime.set_timecode(
                selectors, value, target, selector_indexes=selector_indexes
            ),
        )

    def drag_with_retry(
        self,
        selectors: list[str],
        delta_x: float,
        delta_y: float,
        duration: float,
        target: str,
        *,
        selector_indexes: dict[str, int] | None = None,
    ) -> ExecutionResult:
        return self._execute_with_retry(
            action="drag",
            selectors=selectors,
            target=target,
            call=lambda: self.runtime.drag(
                selectors,
                delta_x,
                delta_y,
                duration,
                target,
                selector_indexes=selector_indexes,
            ),
        )

    def press_key_with_retry(self, key: str, target: str) -> ExecutionResult:
        try:
            used_selector = self.runtime.press_key(key, target)
            return ExecutionResult(used_selector=used_selector, fallback_level=0)
        except Exception as first_error:
            try:
                self.runtime.wait(self.wait_seconds)
                used_selector = self.runtime.press_key(key, target)
                return ExecutionResult(
                    used_selector=used_selector,
                    fallback_level=0,
                    retry_count=1,
                    notes=["keyboard_retry"],
                )
            except Exception as second_error:
                raise ReplanRequired(
                    f"press_key target {target!r} failed after retry and needs replan.",
                    reason="runtime_exception",
                    original_error=(
                        f"{type(first_error).__name__}: {first_error}; "
                        f"{type(second_error).__name__}: {second_error}"
                    ),
                    retry_count=1,
                ) from second_error

    def refresh_and_requery(self) -> None:
        if self.snapshot_fn is not None:
            self.snapshot_fn()

    def _execute_with_retry(
        self,
        *,
        action: str,
        selectors: list[str],
        target: str,
        call: Callable[[], str],
    ) -> ExecutionResult:
        if not selectors:
            raise ReplanRequired(
                f"No selectors available for {action} target {target!r}.",
                reason="selector_miss",
            )

        try:
            used_selector = call()
            return ExecutionResult(
                used_selector=used_selector,
                fallback_level=_fallback_level(selectors, used_selector),
            )
        except Exception as first_error:
            try:
                self.runtime.wait(self.wait_seconds)
                self.refresh_and_requery()
                used_selector = call()
                return ExecutionResult(
                    used_selector=used_selector,
                    fallback_level=_fallback_level(selectors, used_selector),
                    retry_count=1,
                    refreshed_snapshot=self.snapshot_fn is not None,
                    notes=["retry_after_refresh_and_requery"],
                )
            except Exception as second_error:
                raise ReplanRequired(
                    (
                        f"{action} target {target!r} failed after retry; "
                        "current DOM/selector state is uncertain and needs replan."
                    ),
                    reason="selector_miss",
                    original_error=(
                        f"{type(first_error).__name__}: {first_error}; "
                        f"{type(second_error).__name__}: {second_error}"
                    ),
                    retry_count=1,
                ) from second_error


def _fallback_level(selectors: list[str], used_selector: str) -> int:
    try:
        return selectors.index(used_selector)
    except ValueError:
        return -1

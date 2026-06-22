from __future__ import annotations

import pytest

from app.resilience.executor import ReplanRequired
from app.resilience.executor import ResilientActionExecutor


class FlakyRuntime:
    def __init__(self, *, fail_times: int) -> None:
        self.fail_times = fail_times
        self.click_calls = 0
        self.wait_calls = 0

    def click(self, selectors: list[str], target: str, *, selector_indexes: dict[str, int] | None = None) -> str:
        self.click_calls += 1
        if self.click_calls <= self.fail_times:
            raise RuntimeError("element stale")
        return selectors[-1]

    def wait(self, seconds: float) -> None:
        self.wait_calls += 1


class ComplexActionRuntime:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def set_range(self, selectors, value, target, *, selector_indexes=None):
        self.calls.append(("set_range", selectors, value, target))
        return selectors[0]

    def double_click(self, selectors, target, *, selector_indexes=None):
        self.calls.append(("double_click", selectors, target))
        return selectors[0]

    def set_timecode(self, selectors, value, target, *, selector_indexes=None):
        self.calls.append(("set_timecode", selectors, value, target))
        return selectors[0]

    def drag(self, selectors, delta_x, delta_y, duration, target, *, selector_indexes=None):
        self.calls.append(("drag", selectors, delta_x, delta_y, duration, target))
        return selectors[0]

    def press_key(self, key, target):
        self.calls.append(("press_key", key, target))
        return f"key:{key}"

    def wait(self, seconds: float) -> None:
        self.calls.append(("wait", seconds))


def test_executor_retries_and_records_refresh() -> None:
    runtime = FlakyRuntime(fail_times=1)
    snapshots = 0

    def snapshot() -> list[dict]:
        nonlocal snapshots
        snapshots += 1
        return []

    result = ResilientActionExecutor(runtime, snapshot_fn=snapshot).click_with_fallback(
        ["@id=missing", "@id=ok"],
        "OK",
    )

    assert result.used_selector == "@id=ok"
    assert result.fallback_level == 1
    assert result.retry_count == 1
    assert result.refreshed_snapshot is True
    assert snapshots == 1
    assert runtime.wait_calls == 1


def test_executor_requires_replan_when_selectors_are_missing() -> None:
    runtime = FlakyRuntime(fail_times=0)

    with pytest.raises(ReplanRequired) as exc_info:
        ResilientActionExecutor(runtime).click_with_fallback([], "Missing")

    assert exc_info.value.reason == "selector_miss"


def test_executor_requires_replan_after_retry_failure() -> None:
    runtime = FlakyRuntime(fail_times=99)

    with pytest.raises(ReplanRequired) as exc_info:
        ResilientActionExecutor(runtime).click_with_fallback(["@id=gone"], "Gone")

    assert exc_info.value.reason == "selector_miss"
    assert exc_info.value.retry_count == 1
    assert "element stale" in (exc_info.value.original_error or "")


def test_executor_routes_complex_editor_actions() -> None:
    runtime = ComplexActionRuntime()
    executor = ResilientActionExecutor(runtime)

    range_result = executor.execute(
        {"type": "set_range", "target": "Speed", "value": "1.5"},
        ["css:input[type=range]"],
    )
    double_result = executor.execute(
        {"type": "double_click", "target": "sample.mp4"},
        ["css:.media-card"],
    )
    time_result = executor.execute(
        {"type": "set_timecode", "target": "Trim start", "value": "00:02.00"},
        ["css:.time-stepper"],
    )
    drag_result = executor.execute(
        {"type": "drag", "target": "Trim handle", "delta_x": 40, "delta_y": -2, "duration": 0.8},
        ["css:.trim-handle"],
    )
    key_result = executor.execute(
        {"type": "press_key", "target": "selected clip", "value": "DELETE"},
        [],
    )

    assert range_result.used_selector == "css:input[type=range]"
    assert double_result.used_selector == "css:.media-card"
    assert time_result.used_selector == "css:.time-stepper"
    assert drag_result.used_selector == "css:.trim-handle"
    assert key_result.used_selector == "key:DELETE"
    assert ("drag", ["css:.trim-handle"], 40.0, -2.0, 0.8, "Trim handle") in runtime.calls

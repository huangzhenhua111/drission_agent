from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CapturedAction:
    step_index: int
    type: str
    target: str
    chosen_selector: str | None = None
    fallback_selectors: list[str] | None = None
    value: str | None = None
    comment: str | None = None


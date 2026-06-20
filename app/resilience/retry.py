from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar


T = TypeVar("T")


def retry_once(fn: Callable[[], T]) -> T:
    try:
        return fn()
    except Exception:
        return fn()


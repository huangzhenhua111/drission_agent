from __future__ import annotations


def assert_url_contains(current_url: str, expected: str) -> None:
    if expected not in current_url:
        raise AssertionError(f"URL assertion failed: expected {expected!r} in {current_url!r}")


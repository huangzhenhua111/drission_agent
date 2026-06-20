from __future__ import annotations

from typing import Protocol


class BrowserRuntime(Protocol):
    def start(self) -> None: ...

    def goto(self, url: str) -> None: ...

    def snapshot(self) -> list[dict]: ...

    def click(
        self,
        selectors: list[str],
        target: str,
        *,
        selector_indexes: dict[str, int] | None = None,
    ) -> str: ...

    def input(
        self,
        selectors: list[str],
        value: str,
        target: str,
        *,
        selector_indexes: dict[str, int] | None = None,
    ) -> str: ...

    def select(
        self,
        selectors: list[str],
        value: str,
        by: str,
        target: str,
        *,
        selector_indexes: dict[str, int] | None = None,
    ) -> str: ...

    def upload(
        self,
        selectors: list[str],
        path: str,
        target: str,
        *,
        selector_indexes: dict[str, int] | None = None,
    ) -> str: ...

    def wait(self, seconds: float) -> None: ...

    def state(self) -> dict: ...

    def screenshot(self, path: str) -> None: ...

    def close(self) -> None: ...

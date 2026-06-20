from __future__ import annotations


class CaptureError(RuntimeError):
    pass


class AuthenticationRequired(CaptureError):
    def __init__(self, message: str, *, step_index: int, url: str | None = None) -> None:
        super().__init__(message)
        self.step_index = step_index
        self.url = url


class PlanMismatch(CaptureError):
    pass

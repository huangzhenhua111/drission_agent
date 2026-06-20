from __future__ import annotations


class DebugLoop:
    def run(self, script_path: str, max_retries: int = 3) -> str:
        raise NotImplementedError("Debug loop lands in the next phase.")


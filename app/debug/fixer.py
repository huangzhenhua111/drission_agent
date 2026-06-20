from __future__ import annotations


class ScriptFixer:
    def fix(self, failure_context: dict) -> str:
        raise NotImplementedError("Script fixer lands in the next phase.")


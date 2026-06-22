from __future__ import annotations

import json
from pathlib import Path

from app.debug.failure_context import classify_failure
from app.debug.failure_context import context_from_script_run
from app.debug.fixer import ScriptFixer
from app.debug.runner import DebugRunner


class DebugLoop:
    def __init__(
        self,
        *,
        runner: DebugRunner | None = None,
        fixer: ScriptFixer | None = None,
        output_dir: str | Path | None = None,
    ) -> None:
        self.runner = runner or DebugRunner()
        self.fixer = fixer or ScriptFixer()
        self.output_dir = Path(output_dir).resolve() if output_dir else None

    def collect_attempt(
        self,
        script_path: str,
        *,
        attempt_index: int = 1,
        timeout: int = 120,
        env: dict[str, str] | None = None,
    ) -> dict:
        script_run = self.runner.run_script(script_path, timeout=timeout, env=env)
        attempt = {
            "schema_version": 1,
            "attempt_index": attempt_index,
            "script_path": str(Path(script_path).resolve()),
            "script_run": script_run,
            "success": bool(script_run.get("success")),
            "failure_context": None,
            "classification": None,
            "fix_request": None,
        }
        if not script_run.get("success"):
            failure_context = script_run.get("failure_context") or context_from_script_run(script_run)
            classification = script_run.get("classification") or classify_failure(failure_context)
            attempt["failure_context"] = failure_context
            attempt["classification"] = classification
            attempt["fix_request"] = self.fixer.build_fix_request(failure_context, classification)
        self._write_attempt_artifacts(attempt)
        return attempt

    def run(self, script_path: str, max_retries: int = 3) -> str:
        attempt = self.collect_attempt(script_path, attempt_index=1)
        if attempt["success"]:
            return str(Path(script_path).resolve())
        category = (attempt.get("classification") or {}).get("category") or "unknown"
        raise NotImplementedError(
            f"DebugLoop patch/retry lands in the next phase; first attempt classified failure as {category!r}."
        )

    def _write_attempt_artifacts(self, attempt: dict) -> None:
        if self.output_dir is None:
            return
        self.output_dir.mkdir(parents=True, exist_ok=True)
        index = int(attempt.get("attempt_index") or 1)
        _write_json(self.output_dir / f"debug_attempt_{index:02d}.json", attempt)
        if attempt.get("failure_context"):
            _write_json(self.output_dir / f"failure_context_{index:02d}.json", attempt["failure_context"])
        if attempt.get("classification"):
            _write_json(self.output_dir / f"classification_{index:02d}.json", attempt["classification"])
        if attempt.get("fix_request"):
            _write_json(self.output_dir / f"fix_request_{index:02d}.json", attempt["fix_request"])


def _write_json(path: Path, data: object) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

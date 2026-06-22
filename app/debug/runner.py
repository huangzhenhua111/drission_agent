from __future__ import annotations

import json
import subprocess
import sys
import os
import signal
from pathlib import Path

from app.debug.failure_context import classify_failure
from app.debug.failure_context import context_from_script_run


class DebugRunner:
    def run_script(
        self,
        script_path: str,
        *,
        timeout: int = 120,
        env: dict[str, str] | None = None,
    ) -> dict:
        path = Path(script_path).resolve()
        process_env = os.environ.copy()
        if env:
            process_env.update(env)
        process = subprocess.Popen(
            [sys.executable, str(path)],
            cwd=str(path.parent),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=process_env,
            start_new_session=(os.name != "nt"),
        )
        try:
            stdout_bytes, stderr_bytes = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            if os.name == "nt":
                process.kill()
            else:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
            stdout_bytes, stderr_bytes = process.communicate()
            raise subprocess.TimeoutExpired(
                [sys.executable, str(path)],
                timeout,
                output=stdout_bytes.decode("utf-8", errors="replace"),
                stderr=stderr_bytes.decode("utf-8", errors="replace"),
            )
        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")
        metrics_path = path.parent / "generated_script_metrics.json"
        metrics = None
        if metrics_path.exists():
            try:
                metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                metrics = {"error": "generated_script_metrics.json is not valid JSON"}
        result = {
            "script_path": str(path),
            "returncode": process.returncode,
            "stdout": stdout,
            "stderr": stderr,
            "success": process.returncode == 0,
            "metrics_path": str(metrics_path) if metrics_path.exists() else None,
            "metrics": metrics,
        }
        if process.returncode != 0:
            failure_context = context_from_script_run(result)
            result["failure_context"] = failure_context
            result["classification"] = classify_failure(failure_context)
        return result

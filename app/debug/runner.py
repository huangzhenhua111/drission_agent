from __future__ import annotations

import subprocess
import sys
import os
from pathlib import Path


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
        completed = subprocess.run(
            [sys.executable, str(path)],
            cwd=str(path.parent),
            text=True,
            capture_output=True,
            timeout=timeout,
            env=process_env,
        )
        return {
            "script_path": str(path),
            "returncode": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
            "success": completed.returncode == 0,
        }

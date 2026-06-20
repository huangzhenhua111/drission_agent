from __future__ import annotations

import argparse
import json
from pathlib import Path
from datetime import datetime

from app.config import load_settings
from app.debug.runner import DebugRunner
from app.generation.capture_runner import CaptureRunner
from app.generation.exceptions import AuthenticationRequired
from app.generation.planner import LLMPlanner
from app.generation.planner import MockPlanner
from app.generation.script_writer import ScriptWriter
from app.runtime.drission_runtime import DrissionRuntime
from app.validation.assertions import validate_capture_success
from app.validation.static_checks import validate_generated_script

def read_task(args: argparse.Namespace) -> str:
    if args.task_file:
        return Path(args.task_file).read_text(encoding="utf-8").strip()
    if args.task:
        return args.task.strip()
    raise SystemExit("Provide a task string or --task-file.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.cli",
        description="Generate and verify DrissionPage automation scripts.",
    )
    parser.add_argument("task", nargs="?", help="Natural-language automation task.")
    parser.add_argument("--task-file", help="Path to a UTF-8 task file.")
    parser.add_argument("--mock-llm", action="store_true", help="Use deterministic local mock plans.")
    parser.add_argument("--plan-only", action="store_true", help="Stop after planning.")
    parser.add_argument("--capture-only", action="store_true", help="Stop after capture.")
    parser.add_argument("--no-debug", action="store_true", help="Skip the debug loop.")
    parser.add_argument(
        "--wait-for-login",
        action="store_true",
        help="Monitor manual login when an authentication page is detected, then continue automatically.",
    )
    parser.add_argument(
        "--login-timeout",
        type=int,
        default=300,
        help="Seconds to wait for manual login before failing.",
    )
    parser.add_argument("--max-retries", type=int, default=3, help="Maximum debug repair attempts.")
    parser.add_argument("--max-replans", type=int, default=1, help="Maximum LLM replans during capture.")
    parser.add_argument(
        "--action-delay",
        type=float,
        default=2,
        help="Seconds to pause after each captured/generated action for visual debugging.",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    task = read_task(args)

    settings = load_settings()
    browser_profile_path = str(DrissionRuntime(settings).browser_profile_path())
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = settings.output_dir / run_id
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "task.txt").write_text(task, encoding="utf-8")

    planner = MockPlanner() if args.mock_llm else LLMPlanner()
    try:
        plan = planner.plan(task)
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from exc
    _write_json(output_dir / "action_plan.json", plan.to_dict())

    print(f"Task loaded: {task}")
    print(f"Run directory: {output_dir}")
    print(f"Planned steps: {len(plan.steps)}")

    if args.plan_only:
        print("Plan-only complete.")
        return 0

    if args.capture_only:
        runner = CaptureRunner(
            output_dir=output_dir,
            wait_for_login=args.wait_for_login,
            login_timeout_seconds=args.login_timeout,
            max_replans=args.max_replans,
            planner=planner,
            action_delay_seconds=args.action_delay,
        )
        try:
            captured = runner.run(plan)
        except AuthenticationRequired as exc:
            raise SystemExit(str(exc)) from exc
        except RuntimeError as exc:
            raise SystemExit(str(exc)) from exc
        assertion_issues = validate_capture_success(
            task=task,
            captured_actions=captured,
            success_assertions=plan.success_assertions,
        )
        _write_json(output_dir / "capture_assertions.json", {"issues": assertion_issues, "passed": not assertion_issues})
        if assertion_issues:
            raise SystemExit(f"Capture assertions failed: {assertion_issues}")
        print(f"Captured actions: {len(captured)}")
        print("Capture complete.")
        return 0

    runner = CaptureRunner(
        output_dir=output_dir,
        wait_for_login=args.wait_for_login,
        login_timeout_seconds=args.login_timeout,
        max_replans=args.max_replans,
        planner=planner,
        action_delay_seconds=args.action_delay,
    )
    try:
        captured = runner.run(plan)
    except AuthenticationRequired as exc:
        raise SystemExit(str(exc)) from exc
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from exc
    assertion_issues = validate_capture_success(
        task=task,
        captured_actions=captured,
        success_assertions=plan.success_assertions,
    )
    _write_json(output_dir / "capture_assertions.json", {"issues": assertion_issues, "passed": not assertion_issues})
    if assertion_issues:
        raise SystemExit(f"Capture assertions failed: {assertion_issues}")
    print(f"Captured actions: {len(captured)}")
    script_text = ScriptWriter().render(
        captured,
        browser_user_data_path=browser_profile_path,
        browser_debug_port=settings.browser_debug_port,
        wait_for_login=args.wait_for_login,
        login_timeout_seconds=args.login_timeout,
        action_delay_seconds=args.action_delay,
    )
    script_path = output_dir / "generated_script.py"
    script_path.write_text(script_text, encoding="utf-8")
    issues = validate_generated_script(script_text)
    _write_json(output_dir / "static_check.json", {"issues": issues, "passed": not issues})
    if issues:
        raise SystemExit(f"Generated script failed static checks: {issues}")
    print(f"Generated script: {script_path}")

    if args.no_debug:
        print("Generated script; skipped execution because --no-debug was provided.")
        return 0

    result = DebugRunner().run_script(
        str(script_path),
        env={
            "BROWSER_USER_DATA_PATH": browser_profile_path,
            "BROWSER_DEBUG_PORT": str(settings.browser_debug_port),
            "GENERATED_SCRIPT_WAIT_FOR_LOGIN": "1" if args.wait_for_login else "0",
            "ACTION_DELAY_SECONDS": str(args.action_delay),
        },
    )
    _write_json(output_dir / "script_run.json", result)
    print(f"Generated script returncode: {result['returncode']}")
    if not result["success"]:
        raise SystemExit("Generated script execution failed. See script_run.json.")
    print("Generated script executed successfully.")
    return 0


def _write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import argparse
import json
import os
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
from app.validation.performance_report import build_performance_report
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
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "Wait for manual login when authentication is detected, then continue "
            "automatically (default: enabled; use --no-wait-for-login to fail fast)."
        ),
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
        default=0,
        help=(
            "Seconds to pause after each captured/generated action for visual debugging "
            "(default: 0; set a positive value for demos)."
        ),
    )
    parser.add_argument(
        "--debug-artifacts",
        action="store_true",
        help="Persist verbose raw DOM snapshots on successful capture steps.",
    )
    parser.add_argument(
        "--keep-browser-open",
        action="store_true",
        help="Leave the headed browser open after capture/debug finishes for visual inspection.",
    )
    browser_mode = parser.add_mutually_exclusive_group()
    browser_mode.add_argument("--headless", action="store_true", help="Run Chrome without a visible window.")
    browser_mode.add_argument("--headed", action="store_true", help="Run Chrome with a visible UI (for WSLg/local debugging).")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    task = read_task(args)

    if args.headless:
        os.environ["BROWSER_HEADLESS"] = "1"
    elif args.headed:
        os.environ["BROWSER_HEADLESS"] = "0"
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
            close_on_finish=not args.keep_browser_open,
            debug_artifacts=args.debug_artifacts,
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
        capture_assertions = {"issues": assertion_issues, "passed": not assertion_issues}
        _write_json(output_dir / "capture_assertions.json", capture_assertions)
        _write_performance_report(
            output_dir=output_dir,
            task=task,
            captured_actions=captured,
            capture_assertions=capture_assertions,
            success_assertions=plan.success_assertions,
        )
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
        close_on_finish=not args.keep_browser_open,
        debug_artifacts=args.debug_artifacts,
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
    capture_assertions = {"issues": assertion_issues, "passed": not assertion_issues}
    _write_json(output_dir / "capture_assertions.json", capture_assertions)
    _write_performance_report(
        output_dir=output_dir,
        task=task,
        captured_actions=captured,
        capture_assertions=capture_assertions,
        success_assertions=plan.success_assertions,
    )
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
            "BROWSER_HEADLESS": "1" if settings.browser_headless else "0",
            "GENERATED_SCRIPT_WAIT_FOR_LOGIN": "1" if args.wait_for_login else "0",
            "ACTION_DELAY_SECONDS": str(args.action_delay),
        },
    )
    _write_json(output_dir / "script_run.json", result)
    _write_performance_report(
        output_dir=output_dir,
        task=task,
        captured_actions=captured,
        capture_assertions=capture_assertions,
        success_assertions=plan.success_assertions,
        script_run=result,
    )
    print(f"Generated script returncode: {result['returncode']}")
    if not result["success"]:
        raise SystemExit("Generated script execution failed. See script_run.json.")
    print("Generated script executed successfully.")
    return 0


def _write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_performance_report(
    *,
    output_dir: Path,
    task: str,
    captured_actions: list[dict],
    capture_assertions: dict,
    success_assertions: list[dict] | None = None,
    script_run: dict | None = None,
) -> None:
    trace_path = output_dir / "generation_trace.json"
    generation_trace = []
    if trace_path.exists():
        generation_trace = json.loads(trace_path.read_text(encoding="utf-8"))
    report = build_performance_report(
        task=task,
        captured_actions=captured_actions,
        generation_trace=generation_trace,
        capture_assertions=capture_assertions,
        success_assertions=success_assertions or [],
        script_run=script_run,
        snapshot_stats=_snapshot_stats(output_dir),
    )
    _write_json(output_dir / "performance_report.json", report)


def _snapshot_stats(output_dir: Path) -> dict:
    snapshot_dir = output_dir / "dom_snapshots"
    if not snapshot_dir.exists():
        return {}
    files = sorted(snapshot_dir.glob("*.json"))
    total_bytes = 0
    candidate_counts: list[int] = []
    for path in files:
        total_bytes += path.stat().st_size
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        candidates = data.get("candidates") if isinstance(data, dict) else None
        if isinstance(candidates, list):
            candidate_counts.append(len(candidates))
    return {
        "file_count": len(files),
        "total_bytes": total_bytes,
        "candidate_count_min": min(candidate_counts) if candidate_counts else None,
        "candidate_count_max": max(candidate_counts) if candidate_counts else None,
        "candidate_count_avg": (
            round(sum(candidate_counts) / len(candidate_counts), 2)
            if candidate_counts
            else None
        ),
    }


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import json

from app.cli import _snapshot_stats
from app.cli import build_parser


def test_manual_login_wait_is_enabled_by_default() -> None:
    args = build_parser().parse_args(["do something"])

    assert args.wait_for_login is True


def test_manual_login_wait_can_be_disabled_explicitly() -> None:
    args = build_parser().parse_args(["--no-wait-for-login", "do something"])

    assert args.wait_for_login is False


def test_action_delay_defaults_to_zero_and_debug_artifacts_are_opt_in() -> None:
    args = build_parser().parse_args(["do something"])

    assert args.action_delay == 0
    assert args.debug_artifacts is False

    debug_args = build_parser().parse_args(["--action-delay", "1.5", "--debug-artifacts", "do something"])

    assert debug_args.action_delay == 1.5
    assert debug_args.debug_artifacts is True


def test_snapshot_stats_summarizes_dom_snapshot_files(tmp_path) -> None:
    snapshot_dir = tmp_path / "dom_snapshots"
    snapshot_dir.mkdir()
    (snapshot_dir / "step_00.json").write_text(
        json.dumps({"candidates": [{"id": "a"}, {"id": "b"}]}),
        encoding="utf-8",
    )
    (snapshot_dir / "raw_step_00.json").write_text(
        json.dumps({"candidates": [{"id": "a"}]}),
        encoding="utf-8",
    )

    stats = _snapshot_stats(tmp_path)

    assert stats["file_count"] == 2
    assert stats["candidate_count_min"] == 1
    assert stats["candidate_count_max"] == 2
    assert stats["candidate_count_avg"] == 1.5
    assert stats["total_bytes"] > 0

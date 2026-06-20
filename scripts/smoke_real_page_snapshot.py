from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.generation.candidate_compactor import build_grounding_candidates
from app.generation.candidate_compactor import compact_candidates
from app.runtime.drission_runtime import DrissionRuntime


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Snapshot a real page into outputs/.")
    parser.add_argument("url", help="Real page URL to open and snapshot.")
    parser.add_argument("--wait", type=float, default=5.0, help="Seconds to wait after doc_loaded.")
    parser.add_argument("--label", default="real_page_snapshot", help="Output directory prefix.")
    parser.add_argument(
        "--action",
        choices=["click", "input", "select", "upload"],
        help="Write candidates.json as an action-specific grounding view.",
    )
    parser.add_argument("--target", default="", help="Optional target text for action-specific ranking.")
    return parser


def write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    args = build_parser().parse_args()
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = ROOT / "outputs" / f"{args.label}_{run_id}"
    output_dir.mkdir(parents=True, exist_ok=True)

    runtime = DrissionRuntime()
    try:
        runtime.goto(args.url)
        if args.wait > 0:
            runtime.page.wait(args.wait)
        state = runtime.state()
        raw_candidates = runtime.snapshot()
        if args.action:
            step = {"type": args.action, "target": args.target}
            candidates = build_grounding_candidates(step, raw_candidates)
        else:
            candidates = compact_candidates(raw_candidates)
        screenshot_path = output_dir / "screenshot.png"
        runtime.screenshot(str(screenshot_path))

        write_json(output_dir / "state.json", state)
        write_json(output_dir / "raw_candidates.json", raw_candidates)
        write_json(output_dir / "candidates.json", candidates)
        write_json(
            output_dir / "summary.json",
            {
                "url": args.url,
                "title": state.get("title"),
                "current_url": state.get("url"),
                "raw_candidate_count": len(raw_candidates),
                "candidate_count": len(candidates),
                "action": args.action,
                "target": args.target,
                "screenshot": str(screenshot_path),
            },
        )

        print(f"output_dir: {output_dir}")
        print(f"title: {state.get('title')}")
        print(f"current_url: {state.get('url')}")
        print(f"raw_candidate_count: {len(raw_candidates)}")
        print(f"candidate_count: {len(candidates)}")
        for candidate in candidates[:20]:
            print(
                candidate.get("candidate_id"),
                candidate.get("tag"),
                "id=",
                candidate.get("id"),
                "name=",
                candidate.get("name"),
                "text=",
                candidate.get("text"),
                "selectors=",
                candidate.get("selector_candidates", [])[:3],
            )
        return 0
    finally:
        runtime.close()


if __name__ == "__main__":
    raise SystemExit(main())

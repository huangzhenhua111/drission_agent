from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.runtime.drission_runtime import DrissionRuntime


REAL_WEB_FORM_URL = "https://www.selenium.dev/selenium/web/web-form.html"


REQUIRED_CANDIDATES = {
    "text_input": lambda c: c.get("id") == "my-text-id" and c.get("name") == "my-text",
    "password": lambda c: c.get("name") == "my-password" and c.get("type") == "password",
    "textarea": lambda c: c.get("name") == "my-textarea" and c.get("tag") == "textarea",
    "select": lambda c: c.get("name") == "my-select" and c.get("tag") == "select",
    "file": lambda c: c.get("name") == "my-file" and c.get("type") == "file",
    "checkbox": lambda c: c.get("id") == "my-check-1" and c.get("type") == "checkbox",
    "radio": lambda c: c.get("id") == "my-radio-1" and c.get("type") == "radio",
    "submit": lambda c: (
        c.get("tag") == "button" and c.get("type") == "submit" and c.get("text") == "Submit"
    ),
}


def main() -> int:
    runtime = DrissionRuntime()
    try:
        runtime.goto(REAL_WEB_FORM_URL)
        state = runtime.state()
        candidates = runtime.snapshot()
        found = {}

        for name, predicate in REQUIRED_CANDIDATES.items():
            matches = [candidate for candidate in candidates if predicate(candidate)]
            if not matches:
                raise AssertionError(f"missing required real-page candidate: {name}")
            found[name] = matches[0]

        print(f"title: {state['title']}")
        print(f"url: {state['url']}")
        print(f"candidate_count: {len(candidates)}")

        for name, candidate in found.items():
            selectors = candidate["selector_candidates"]
            _, used_selector = runtime.find_first(
                selectors,
                target=name,
                require_displayed=(name != "file"),
            )
            print(
                f"{name}: candidate={candidate['candidate_id']} "
                f"used_selector={used_selector} selector_count={len(selectors)}"
            )

        runtime.input(found["text_input"]["selector_candidates"], "hello-real-dom", "text input")
        runtime.select(found["select"]["selector_candidates"], "Two", "text", "select menu")
        print("action_check: input and select succeeded")
        return 0
    finally:
        runtime.close()


if __name__ == "__main__":
    raise SystemExit(main())

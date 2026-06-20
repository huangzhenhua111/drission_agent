from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_required_project_files_exist() -> None:
    required = [
        "requirements.txt",
        ".env.example",
        "README.md",
        "app/cli.py",
        "app/config.py",
        "scripts/smoke_python.py",
        "scripts/smoke_drission.py",
        "scripts/smoke_openai.py",
        "examples/local_search/task.txt",
        "examples/local_search/site/index.html",
        "examples/local_form/task.txt",
        "examples/local_form/site/index.html",
        "examples/real_selenium_web_form/task.txt",
    ]

    missing = [path for path in required if not (ROOT / path).exists()]
    assert missing == []


def test_final_script_forbidden_tokens_are_listed() -> None:
    from app.validation.static_checks import find_forbidden_tokens

    script = "from DrissionPage import ChromiumPage\n# no agent dependency\n"
    assert find_forbidden_tokens(script) == []
    assert "openai" in find_forbidden_tokens("import openai\n")


def test_example_tasks_are_non_empty() -> None:
    task_files = [
        ROOT / "examples/local_search/task.txt",
        ROOT / "examples/local_form/task.txt",
        ROOT / "examples/real_selenium_web_form/task.txt",
    ]
    for task_file in task_files:
        assert task_file.read_text(encoding="utf-8").strip()


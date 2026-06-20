from __future__ import annotations


FORBIDDEN_TOKENS = [
    "OPENAI_API_KEY",
    "sk-",
    "from app",
    "import app",
    "openai",
    "langchain",
    "dotenv",
]


def find_forbidden_tokens(script_text: str) -> list[str]:
    lowered = script_text.lower()
    return [token for token in FORBIDDEN_TOKENS if token.lower() in lowered]


def validate_generated_script(script_text: str) -> list[str]:
    issues = []
    try:
        compile(script_text, "generated_script.py", "exec")
    except SyntaxError as exc:
        issues.append(f"syntax error: {exc}")
    forbidden = find_forbidden_tokens(script_text)
    if forbidden:
        issues.append(f"forbidden tokens: {', '.join(forbidden)}")
    required_snippets = [
        "from DrissionPage import ChromiumOptions, ChromiumPage",
        "def find_first(",
        "fallback_selectors",
        "selector_metadata",
        "page.wait.ele_displayed",
    ]
    for snippet in required_snippets:
        if snippet not in script_text:
            issues.append(f"missing required snippet: {snippet}")
    return issues

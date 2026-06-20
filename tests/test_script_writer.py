from __future__ import annotations

from app.generation.script_writer import ScriptWriter
from app.validation.static_checks import validate_generated_script


def test_script_writer_renders_standalone_drission_script() -> None:
    script = ScriptWriter().render(
        [
            {
                "step_index": 0,
                "type": "goto",
                "target": "Example",
                "url": "https://example.com",
                "fallback_selectors": [],
            },
            {
                "step_index": 1,
                "type": "click",
                "target": "Submit",
                "chosen_selector": "text=Submit",
                "fallback_selectors": ["text=Submit", "css:button[type='submit']"],
                "selector_metadata": [
                    {"selector": "text=Submit", "index": 0, "match_count": 1, "unique": True}
                ],
            },
            {
                "step_index": 2,
                "type": "click",
                "target": "Visual fallback",
                "visual_position": {"x": 100, "y": 200},
                "fallback_selectors": [],
                "selector_metadata": [],
            },
            {
                "step_index": 3,
                "type": "upload",
                "target": "Add Image",
                "path": "fixture.png",
                "fallback_selectors": ["css:input[type='file']"],
                "selector_metadata": [
                    {
                        "selector": "css:input[type='file']",
                        "index": 1,
                        "match_count": 2,
                        "unique": False,
                    }
                ],
            },
        ],
        browser_user_data_path=r"C:\workspace\drission_agent\outputs\browser_profiles\chrome",
        browser_debug_port=19222,
        wait_for_login=True,
        login_timeout_seconds=123,
        action_delay_seconds=1.5,
    )

    compile(script, "generated_script.py", "exec")
    assert "from app" not in script
    assert "def find_first(" in script
    assert "selector_metadata" in script
    assert "css:input[type='file']" in script
    assert "DEFAULT_BROWSER_USER_DATA_PATH" in script
    assert "DEFAULT_BROWSER_DEBUG_PORT = 19222" in script
    assert "DEFAULT_WAIT_FOR_LOGIN = True" in script
    assert "DEFAULT_LOGIN_TIMEOUT_SECONDS = 123" in script
    assert "DEFAULT_ACTION_DELAY_SECONDS = 1.5" in script
    assert "ACTION_DELAY_SECONDS" in script
    assert "DEFAULT_FAST_SELECTOR_TIMEOUT = 0.3" in script
    assert "def ordered_selectors(" in script
    assert "def is_volatile_selector(" in script
    assert "selector_lookup_timeout(selector, timeout)" in script
    assert "def ensure_authenticated(" in script
    assert "def click_at(" in script
    assert "def preview_click_target(" in script
    assert "def verify_action_postcondition(" in script
    assert "Target ready:" in script
    assert "visual_position" in script
    assert "auto_port" not in script
    assert "browser_profiles" in script
    assert validate_generated_script(script) == []

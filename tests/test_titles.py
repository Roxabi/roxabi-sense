from roxabi_sense.util.titles import normalize_title, sanitize_display


def test_strip_thinking_spinner() -> None:
    assert (
        normalize_title("⠋ - Thinking - Silex-Brain Repo Existence Check - grok")
        == "Silex-Brain Repo Existence Check - grok"
    )
    assert (
        normalize_title("⠦ - Responding - Fix feedback package - grok")
        == "Fix feedback package - grok"
    )


def test_waiting_for_response() -> None:
    assert (
        normalize_title("⠙ - Waiting for response… - Hermes Slack kit - grok")
        == "Hermes Slack kit - grok"
    )
    assert (
        normalize_title("Waiting for response... - Task - grok")
        == "Task - grok"
    )


def test_sanitize_strips_escapes() -> None:
    raw = "hi\x1b[2J\x1b[Hworld\nnext"
    assert "\x1b" not in sanitize_display(raw)
    assert "\n" not in sanitize_display(raw)
    assert "hi" in sanitize_display(raw)


def test_stable_plain_title() -> None:
    t = "Spark — Silex - Google Chrome - Mickael"
    assert normalize_title(t) == t

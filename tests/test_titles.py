from roxabi_sense.util.titles import normalize_title, sanitize_display, title_core


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
    assert normalize_title("Waiting for response... - Task - grok") == "Task - grok"


def test_sanitize_strips_escapes() -> None:
    raw = "hi\x1b[2J\x1b[Hworld\nnext"
    assert "\x1b" not in sanitize_display(raw)
    assert "\n" not in sanitize_display(raw)
    assert "hi" in sanitize_display(raw)


def test_stable_plain_title() -> None:
    t = "Spark — Silex - Google Chrome - Mickael"
    assert normalize_title(t) == t


def test_omp_pi_spinner_prefix() -> None:
    raw = "\u03c0 ⢸ Read X"
    assert normalize_title(raw) == "Read X"
    assert title_core(raw) == "read x"


def test_omp_pi_prompt_prefix() -> None:
    raw = "π > PR foo"
    assert normalize_title(raw) == "PR foo"
    assert title_core(raw) == "pr foo"
    assert title_core("π > PR foo - omp") == "pr foo"

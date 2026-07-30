from roxabi_sense.util.titles import normalize_title


def test_strip_thinking_spinner() -> None:
    assert (
        normalize_title("⠋ - Thinking - Silex-Brain Repo Existence Check - grok")
        == "Silex-Brain Repo Existence Check - grok"
    )
    assert (
        normalize_title("⠦ - Responding - Fix feedback package - grok")
        == "Fix feedback package - grok"
    )


def test_stable_plain_title() -> None:
    t = "Spark — Silex - Google Chrome - Mickael"
    assert normalize_title(t) == t

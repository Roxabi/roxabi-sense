from roxabi_sense.cli import main


def test_status_is_scaffold() -> None:
    assert main(["status"]) == 2


def test_version_flag() -> None:
    try:
        main(["--version"])
    except SystemExit as exc:
        assert exc.code == 0

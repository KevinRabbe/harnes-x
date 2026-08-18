from harness_x.cli import build_parser, main


def test_cli_help_parser_exists() -> None:
    parser = build_parser()
    assert parser.prog == "harness-x"


def test_validate_config_command(capsys) -> None:
    rc = main(["validate-config", "configs/default.yaml"])
    assert rc == 0
    assert "valid: system_version=0.1.0-alpha.0" in capsys.readouterr().out

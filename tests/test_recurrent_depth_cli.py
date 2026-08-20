import json

import pytest

from harness_x.reasoning.recurrent_depth import (
    DepthSelectorArtifact,
    RecurrentDepthResearchReport,
)
from harness_x.reasoning.recurrent_depth_cli import main


def test_recurrent_depth_reference_cli_writes_valid_report_and_selector(tmp_path) -> None:
    output = tmp_path / "recurrent-depth"

    code = main(["--backend", "reference", "--output", str(output)])

    assert code == 0
    report = RecurrentDepthResearchReport.model_validate_json(
        (output / "recurrent-depth-report.json").read_text(encoding="utf-8")
    )
    selector = DepthSelectorArtifact.model_validate_json(
        (output / "learned-depth-selector.json").read_text(encoding="utf-8")
    )
    assert report.passed is True
    assert report.evidence_kind == "reference_simulator"
    assert selector == report.learned_selector


def test_recurrent_depth_cli_rejects_huginn_without_explicit_case_file() -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--backend", "huginn", "--allow-remote-code"])

    assert exc.value.code == 2


def test_recurrent_depth_cli_rejects_non_positive_depths() -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--backend", "reference", "--depths", "0", "4"])

    assert exc.value.code == 2

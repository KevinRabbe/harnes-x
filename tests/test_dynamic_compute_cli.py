from harness_x.cli import main
from harness_x.controllers import DynamicComputeComparisonReport, LearnedComputeControllerArtifact


def test_dynamic_compute_reference_benchmark_cli(tmp_path, capsys) -> None:
    output = tmp_path / "dynamic-benchmark"

    code = main([
        "benchmark-dynamic-compute",
        "--output",
        str(output),
    ])

    assert code == 0
    report = DynamicComputeComparisonReport.model_validate_json(
        (output / "dynamic-compute-comparison.json").read_text(encoding="utf-8")
    )
    artifact = LearnedComputeControllerArtifact.model_validate_json(
        (output / "reference-learned-controller.json").read_text(encoding="utf-8")
    )
    assert report.learned_frontier_improved is True
    assert artifact.training_example_count == 21
    assert "learned_frontier_improved" in capsys.readouterr().out

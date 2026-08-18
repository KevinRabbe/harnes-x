"""Load/validate generated grounded self-model curriculum files."""

from __future__ import annotations

from pathlib import Path

from .models import CurriculumDataset, CurriculumManifest, SelfModelExample


def load_curriculum(directory: str | Path) -> CurriculumDataset:
    root = Path(directory)
    manifest = CurriculumManifest.model_validate_json(
        (root / "manifest.json").read_text(encoding="utf-8")
    )
    examples: list[SelfModelExample] = []
    for name in ("train.jsonl", "eval.jsonl"):
        path = root / name
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                examples.append(SelfModelExample.model_validate_json(line))
    return CurriculumDataset(examples=tuple(examples), manifest=manifest)

"""Explicit split policy metadata for self-model curriculum generation.

The implementation currently holds out complete diagnostic fault families rather
than randomly splitting rows. Future architecture-configuration holdouts should be
added here as named policy data, not introduced through random sampling.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from .curriculum import HELD_OUT_FAULT_FAMILIES


class CurriculumSplitPolicy(BaseModel):
    model_config = ConfigDict(frozen=True)

    policy_version: str = "self-model-split-v1"
    random_row_split: bool = False
    held_out_fault_families: tuple[str, ...] = tuple(
        sorted(item.value for item in HELD_OUT_FAULT_FAMILIES)
    )
    holdout_unit: str = "seed_or_fault_family"


DEFAULT_SPLIT_POLICY = CurriculumSplitPolicy()

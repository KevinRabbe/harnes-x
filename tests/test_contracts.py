from datetime import datetime, timezone

from harness_x.core.contracts import Observation
from harness_x.core.ids import SystemVersion, TaskId, TraceId
from harness_x.core.provenance import Provenance, SourceKind, VerificationState


def test_observation_round_trip_preserves_provenance() -> None:
    provenance = Provenance(
        source_kind=SourceKind.TOOL,
        source_ref="tool:test",
        created_at=datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc),
        system_version=SystemVersion(value="0.1.0-alpha.0"),
        trace_id=TraceId.new(),
        verification=VerificationState.VERIFIED,
    )
    original = Observation(
        task_id=TaskId.new(),
        kind="fixture",
        content={"value": 42},
        provenance=provenance,
    )

    restored = Observation.model_validate_json(original.model_dump_json())
    assert restored == original
    assert restored.provenance.trace_id == provenance.trace_id
    assert restored.provenance.verification is VerificationState.VERIFIED

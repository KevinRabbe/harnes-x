from __future__ import annotations

from harness_x.app_server.improvement_observatory import (
    ImprovementObservatoryProjection,
    ObservatoryCampaign,
    ObservatoryCandidate,
    ObservatoryPromotion,
    ObservatoryRollbackEvidence,
)
from harness_x.app_server.improvement_observatory_operator_http_server import _public_projection


def test_public_projection_drops_model_operator_and_filesystem_free_text(tmp_path) -> None:
    secret_rationale = "model rationale includes credential-value"
    secret_terminal = "operator cancellation says credential-value"
    secret_promotion_reason = "operator rollback says credential-value"
    secret_path = str(tmp_path / "private" / "rollback.json")
    projection = ImprovementObservatoryProjection(
        project_id="project_fixture",
        software_version="0.1.0",
        observatory_root_present=True,
        scan_truncated=False,
        candidates=(
            ObservatoryCandidate(
                candidate_id="prev_fixture",
                candidate_kind="procedure_revision",
                status="candidate",
                rationale=secret_rationale,
                source=".harness-x/project-memory/procedure-revisions.json",
            ),
        ),
        promotions=(
            ObservatoryPromotion(
                promotion_id="promotion_fixture",
                candidate_id="candidate_fixture",
                baseline_version="0.1.0",
                status="rolled_back",
                qualification_allowed=True,
                reason=secret_promotion_reason,
                rollback=ObservatoryRollbackEvidence(
                    recorded=True,
                    recorded_sha256="a" * 64,
                    independently_verified=None,
                    verification_detail=f"missing rollback artifact at {secret_path}",
                ),
                source=".harness-x/promotion-record.json",
            ),
        ),
        campaigns=(
            ObservatoryCampaign(
                campaign_id="pcamp_fixture",
                parent_procedure_id="pmem_fixture",
                status="cancelled",
                proposal_attempts=1,
                trial_attempts=1,
                max_candidate_proposals=3,
                max_trial_tasks=6,
                terminal_reason=secret_terminal,
                source=".harness-x/project-memory/procedure-improvement-campaigns.json",
            ),
        ),
    )

    payload = _public_projection(projection, workspace_root=str(tmp_path))
    encoded = str(payload)
    assert payload["candidates"][0]["rationale"] is None
    assert payload["campaigns"][0]["terminal_reason"] is None
    assert payload["promotions"][0]["reason"] == "redacted from public observatory projection"
    assert payload["promotions"][0]["rollback"]["verification_detail"] == (
        "recorded rollback artifact was not independently verified within observatory boundary"
    )
    assert secret_rationale not in encoded
    assert secret_terminal not in encoded
    assert secret_promotion_reason not in encoded
    assert secret_path not in encoded

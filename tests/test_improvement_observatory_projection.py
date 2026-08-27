from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from harness_x.app_server.improvement_observatory import build_improvement_observatory
from harness_x.coding.procedure_improvement_campaign import (
    ProcedureImprovementCampaign,
    ProcedureImprovementCampaignBudget,
    ProcedureImprovementCampaignStoreState,
)
from harness_x.coding.procedure_reliability import (
    ProcedureReliabilityPolicy,
    ProcedureReliabilityRecord,
    ProcedureReliabilityState,
    ProcedureReliabilityStatus,
)
from harness_x.coding.procedure_revision import (
    ProcedureRevisionCandidate,
    ProcedureRevisionPolicy,
    ProcedureRevisionStoreState,
)
from harness_x.core.ids import SystemVersion
from harness_x.improvement.promotion import (
    PromotionQualification,
    PromotionRecord,
    PromotionStatus,
)


def _write(path: Path, model) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(model.model_dump_json(indent=2) + "\n", encoding="utf-8")


def test_canonical_m29_m30_m31_state_projects_weakness_candidate_and_campaign(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    memory = workspace / ".harness-x" / "project-memory"
    now = datetime(2026, 8, 27, tzinfo=timezone.utc)
    project_id = "project_fixture"
    parent_id = "pmem_parent"
    candidate_id = "prev_fixture"

    reliability = ProcedureReliabilityState(
        project_id=project_id,
        revision=3,
        policy=ProcedureReliabilityPolicy(),
        records=(
            ProcedureReliabilityRecord(
                procedure_id=parent_id,
                status=ProcedureReliabilityStatus.SUSPENDED,
                usage_count=5,
                success_count=2,
                failure_count=3,
                consecutive_failures=2,
                suspended_at_support_count=2,
                suspension_reason="consecutive_verified_reuse_failures:2",
                last_episode_id="pepisode_last",
                updated_revision=3,
            ),
        ),
        usage_total=5,
    )
    revision = ProcedureRevisionCandidate(
        candidate_id=candidate_id,
        parent_procedure_id=parent_id,
        parent_content_fingerprint="a" * 64,
        statement="Use a narrower verified procedure.",
        steps=("Inspect failure evidence.", "Apply the bounded repair."),
        rationale="Repeated verified reuse failed.",
        content_fingerprint="b" * 64,
        replacement_memory_key=f"hx-revision/{candidate_id}",
        origin_episode_id="pepisode_origin",
        origin_reliability_revision=3,
        origin_suspension_reason="consecutive_verified_reuse_failures:2",
        success_episode_ids=("pepisode_success",),
        failure_episode_ids=("pepisode_failure",),
        created_at=now,
        created_revision=2,
        updated_revision=4,
    )
    revisions = ProcedureRevisionStoreState(
        project_id=project_id,
        revision=4,
        policy=ProcedureRevisionPolicy(),
        candidates=(revision,),
        validation_total=2,
    )
    campaign = ProcedureImprovementCampaign(
        campaign_id="pcamp_fixture",
        parent_procedure_id=parent_id,
        parent_content_fingerprint="a" * 64,
        origin_reliability_revision=3,
        origin_suspension_reason="consecutive_verified_reuse_failures:2",
        budget=ProcedureImprovementCampaignBudget(
            max_candidate_proposals=3,
            max_trial_tasks=6,
        ),
        proposal_attempts=1,
        trial_attempts=2,
        candidate_ids=(candidate_id,),
        created_at=now,
        updated_at=now,
        created_revision=2,
        updated_revision=5,
    )
    campaigns = ProcedureImprovementCampaignStoreState(
        project_id=project_id,
        revision=5,
        campaigns=(campaign,),
    )

    _write(memory / "procedure-reliability.json", reliability)
    _write(memory / "procedure-revisions.json", revisions)
    _write(memory / "procedure-improvement-campaigns.json", campaigns)

    before = {path.relative_to(workspace): path.read_bytes() for path in workspace.rglob("*") if path.is_file()}
    projection = build_improvement_observatory(project_id="product_project", workspace_root=workspace)
    after = {path.relative_to(workspace): path.read_bytes() for path in workspace.rglob("*") if path.is_file()}

    assert before == after
    assert [(item.procedure_id, item.reason) for item in projection.weaknesses] == [
        (parent_id, "consecutive_verified_reuse_failures:2")
    ]
    assert len(projection.candidates) == 1
    assert projection.candidates[0].candidate_id == candidate_id
    assert projection.candidates[0].candidate_kind == "procedure_revision"
    assert projection.candidates[0].success_count == 1
    assert projection.candidates[0].failure_count == 1
    assert len(projection.campaigns) == 1
    assert projection.campaigns[0].campaign_id == "pcamp_fixture"
    assert projection.campaigns[0].proposal_attempts == 1
    assert projection.campaigns[0].trial_attempts == 2


def test_external_rollback_path_is_recorded_but_never_followed(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    record_root = workspace / ".harness-x" / "system-improvement" / "promotion"
    record_root.mkdir(parents=True)
    outside = tmp_path / "outside-rollback.json"
    secret = "outside-rollback-secret-must-not-be-read"
    outside.write_text(json.dumps({"secret": secret}), encoding="utf-8")

    record = PromotionRecord(
        promotion_id="promotion_fixture",
        candidate_id="candidate_fixture",
        proposal_fingerprint="1" * 64,
        experiment_report_fingerprint="2" * 64,
        baseline_version=SystemVersion(value="0.1.0-alpha.0"),
        baseline_config_sha256="3" * 64,
        promoted_version=SystemVersion(value="0.1.0-alpha.0+improvement.fixture.1"),
        promoted_config_sha256="4" * 64,
        rollback_artifact_path=str(outside),
        rollback_artifact_sha256="5" * 64,
        qualification=PromotionQualification(
            allowed=True,
            reasons=(),
            policy_version="fixture-policy-v1",
        ),
        verification=None,
        status=PromotionStatus.ACTIVE,
        reason="fixture_active_promotion",
    )
    _write(record_root / "promotion-record.json", record)

    projection = build_improvement_observatory(project_id="product_project", workspace_root=workspace)
    assert len(projection.promotions) == 1
    promotion = projection.promotions[0]
    assert promotion.promotion_id == "promotion_fixture"
    assert promotion.rollback.recorded is True
    assert promotion.rollback.recorded_sha256 == "5" * 64
    assert promotion.rollback.independently_verified is None
    assert "rollback artifact" in promotion.rollback.verification_detail
    assert secret not in projection.model_dump_json()
    assert str(outside) not in projection.model_dump_json()

from __future__ import annotations

from decimal import Decimal

import httpx
import pytest
from sqlalchemy import func, select, update

from backend.core.assurance_policy import AssuranceStatus, DISPOSITION_RANK, status_for_score
from backend.database.models import AcceptedInferenceRecord, AuditLogRecord, FindingRecord, RegisteredModelRecord
from backend.main import create_app
from backend.tests.test_evidence import dataset_finding, drift_finding, inference_finding, model_finding


def finding(
    finding_id: str,
    module: str = "dataset_integrity",
    category: str = "TEST_CATEGORY",
    severity: str = "LOW",
    confidence: float = 1.0,
    recommendation: str = "ACCEPT",
    limitations=None,
):
    return {
        "finding_id": finding_id,
        "module": module,
        "asset_type": "dataset",
        "asset_id": "dataset:TEST",
        "category": category,
        "severity": severity,
        "confidence": confidence,
        "reason": "Deterministic test finding.",
        "evidence": [f"source={finding_id}"],
        "recommendation": recommendation,
        "limitations": limitations or [],
    }


async def ingest(client, *items):
    for item in items:
        response = await client.post("/api/evidence", json=item)
        assert response.status_code == 200


@pytest.mark.anyio
async def test_empty_database_is_unknown_and_unavailable(client):
    summary = (await client.get("/api/summary")).json()
    assert summary["overall"] == {
        "assurance_score": None,
        "score_status": "UNAVAILABLE",
        "assessment_coverage": 0.0,
        "disposition": "REVIEW",
        "reason": "No assurance evidence has been ingested.",
        "critical_overrides": [],
        "most_concerning_module": None,
        "critical_override_details": [],
    }
    for module in summary["modules"].values():
        assert module["availability"] == "UNKNOWN"
        assert module["assurance_score"] is None
        assert module["status"] == "UNKNOWN"
        assert module["disposition"] == "REVIEW"


@pytest.mark.anyio
async def test_dataset_only_has_quarter_coverage_and_cannot_accept(client):
    await ingest(client, dataset_finding())
    summary = (await client.get("/api/summary")).json()
    assert summary["overall"]["assessment_coverage"] == 0.25
    assert summary["overall"]["score_status"] == "PROVISIONAL"
    assert summary["overall"]["disposition"] != "ACCEPT"
    assert summary["modules"]["dataset_integrity"]["availability"] == "ASSESSED"
    assert summary["modules"]["model_integrity"]["status"] == "UNKNOWN"


@pytest.mark.anyio
async def test_exact_high_confidence_calculation_is_explained(client):
    await ingest(client, dataset_finding())
    module = (await client.get("/api/summary")).json()["modules"]["dataset_integrity"]
    penalty = module["score_explanation"]["category_penalties"][0]
    assert penalty == {
        "category": "NEAR_DUPLICATE",
        "source_finding_id": "F-DATA-001",
        "severity": "HIGH",
        "confidence": 0.91,
        "severity_penalty": 45.0,
        "penalty": 40.95,
    }
    assert module["score_explanation"]["total_penalty"] == 40.95
    assert module["assurance_score"] == 59.1


@pytest.mark.anyio
async def test_confidence_scales_penalty_proportionally(client):
    await ingest(client, finding("F-1", severity="HIGH", confidence=0.5))
    module = (await client.get("/api/summary")).json()["modules"]["dataset_integrity"]
    assert module["score_explanation"]["total_penalty"] == 22.5
    assert module["assurance_score"] == 77.5


@pytest.mark.anyio
async def test_same_category_uses_max_penalty_only(client):
    await ingest(
        client,
        finding("F-LOW", category="DUP", severity="LOW", confidence=1.0),
        finding("F-HIGH", category="DUP", severity="HIGH", confidence=0.8),
        finding("F-MID", category="DUP", severity="MEDIUM", confidence=1.0),
    )
    explanation = (await client.get("/api/summary")).json()["modules"]["dataset_integrity"]["score_explanation"]
    assert len(explanation["category_penalties"]) == 1
    assert explanation["total_penalty"] == 36.0


@pytest.mark.anyio
async def test_different_categories_accumulate_and_cap_at_100(client):
    await ingest(
        client,
        finding("F-A", category="A", severity="CRITICAL"),
        finding("F-B", category="B", severity="CRITICAL"),
    )
    module = (await client.get("/api/summary")).json()["modules"]["dataset_integrity"]
    assert module["score_explanation"]["total_penalty"] == 100.0
    assert module["assurance_score"] == 0.0


@pytest.mark.parametrize(
    ("score", "expected"),
    [(Decimal("95"), "GOOD"), (Decimal("80"), "WATCH"), (Decimal("59.05"), "REVIEW"), (Decimal("25"), "HIGH_RISK")],
)
def test_score_bands(score, expected):
    assert status_for_score(score) == AssuranceStatus(expected)


@pytest.mark.anyio
async def test_module_score_stays_within_zero_and_hundred(client):
    await ingest(client, finding("F-INFO", severity="INFO", confidence=0.0))
    score = (await client.get("/api/summary")).json()["modules"]["dataset_integrity"]["assurance_score"]
    assert 0 <= score <= 100 and score == 100.0


@pytest.mark.anyio
@pytest.mark.parametrize("category", ["MODEL_SUBSTITUTION", "BACKDOOR_BEHAVIOR"])
async def test_critical_model_categories_force_quarantine(client, category):
    await ingest(client, finding(
        f"F-{category}", module="model_integrity", category=category,
        severity="CRITICAL", recommendation="ACCEPT",
    ))
    summary = (await client.get("/api/summary")).json()
    assert DISPOSITION_RANK[summary["overall"]["disposition"]] >= DISPOSITION_RANK["QUARANTINE"]
    assert category in summary["overall"]["critical_overrides"]


@pytest.mark.anyio
@pytest.mark.parametrize("category", ["OUTPUT_TAMPERING", "REPLAY_DETECTED"])
async def test_critical_inference_categories_cannot_accept(client, category):
    await ingest(client, finding(
        f"F-{category}", module="inference_integrity", category=category,
        severity="CRITICAL", recommendation="ACCEPT",
    ))
    summary = (await client.get("/api/summary")).json()
    assert summary["overall"]["disposition"] != "ACCEPT"
    assert summary["modules"]["inference_integrity"]["disposition"] == "QUARANTINE"
    detail = summary["overall"]["critical_override_details"][0]
    assert detail["asset_disposition"] == "REJECT"
    assert detail["system_effect"] == "QUARANTINE"


@pytest.mark.anyio
async def test_broken_audit_chain_forces_quarantine(client, app):
    await ingest(client, dataset_finding())
    with app.state.session_factory() as session:
        session.execute(update(AuditLogRecord).values(payload_json='{"tampered":true}'))
        session.commit()
    summary = (await client.get("/api/summary")).json()
    assert summary["audit_integrity"]["status"] == "COMPROMISED"
    assert summary["audit_integrity"]["first_broken_audit_id"] == "AUD-00000001"
    assert DISPOSITION_RANK[summary["overall"]["disposition"]] >= DISPOSITION_RANK["QUARANTINE"]


@pytest.mark.anyio
async def test_valid_audit_chain_is_reported(client):
    await ingest(client, dataset_finding())
    audit = (await client.get("/api/summary")).json()["audit_integrity"]
    assert audit["status"] == "VALID" and audit["records_checked"] == 1


@pytest.mark.anyio
async def test_audit_infrastructure_failure_is_unavailable_never_valid(client, app):
    class BrokenAudit:
        def verify(self, _session):
            raise RuntimeError("simulated infrastructure failure")

    app.state.audit_service = BrokenAudit()
    summary = (await client.get("/api/summary")).json()
    assert summary["audit_integrity"]["status"] == "UNAVAILABLE"
    assert summary["overall"]["disposition"] == "REVIEW"


@pytest.mark.anyio
async def test_counts_top_order_latest_order_and_limitations(client):
    items = [
        finding("F-LOW", severity="LOW", confidence=1.0, limitations=["shared", "low-only"]),
        finding("F-CRIT-B", category="B", severity="CRITICAL", confidence=0.7, recommendation="REVIEW", limitations=["shared"]),
        finding("F-CRIT-A", category="C", severity="CRITICAL", confidence=0.9, recommendation="QUARANTINE"),
        finding("F-HIGH", category="D", severity="HIGH", confidence=1.0, recommendation="REJECT"),
    ]
    await ingest(client, *items)
    summary = (await client.get("/api/summary")).json()
    assert summary["finding_counts"] == {"INFO": 0, "LOW": 1, "MEDIUM": 0, "HIGH": 1, "CRITICAL": 2, "total": 4}
    module = summary["modules"]["dataset_integrity"]
    assert module["severity_counts"]["CRITICAL"] == 2
    assert module["category_counts"] == {"B": 1, "C": 1, "D": 1, "TEST_CATEGORY": 1}
    assert [item["finding_id"] for item in module["top_findings"]] == ["F-CRIT-A", "F-CRIT-B", "F-HIGH", "F-LOW"]
    assert [item["finding_id"] for item in summary["latest_findings"]] == list(reversed([item["finding_id"] for item in items]))
    assert summary["limitations"] == ["shared", "low-only"]
    assert summary["recommendation_counts"] == {"ACCEPT": 1, "REVIEW": 1, "QUARANTINE": 1, "REJECT": 1}


@pytest.mark.anyio
async def test_four_module_fixture_has_deterministic_complete_summary(client):
    await ingest(client, dataset_finding(), model_finding(), inference_finding(), drift_finding())
    summary = (await client.get("/api/summary")).json()
    assert summary["overall"]["assessment_coverage"] == 1.0
    assert summary["overall"]["score_status"] == "COMPLETE"
    assert summary["modules"]["dataset_integrity"]["assurance_score"] == 59.1
    assert summary["modules"]["model_integrity"]["assurance_score"] == 33.3
    assert summary["modules"]["inference_integrity"]["assurance_score"] == 25.0
    assert summary["modules"]["distribution_shift"]["assurance_score"] == 83.6
    assert summary["overall"]["assurance_score"] == 45.2
    assert summary["overall"]["disposition"] == "QUARANTINE"
    assert summary["overall"]["most_concerning_module"] == "inference_integrity"
    assert summary["overall"]["critical_overrides"] == ["BACKDOOR_BEHAVIOR", "REPLAY_DETECTED"]
    replay = next(item for item in summary["overall"]["critical_override_details"] if item["category"] == "REPLAY_DETECTED")
    assert replay == {
        "category": "REPLAY_DETECTED",
        "asset_id": "inference:INF-0042",
        "asset_disposition": "REJECT",
        "system_effect": "QUARANTINE",
    }


@pytest.mark.anyio
async def test_summary_is_read_only_across_repeated_calls(client, app):
    await ingest(client, dataset_finding())
    with app.state.session_factory() as session:
        before = {
            "findings": session.scalar(select(func.count()).select_from(FindingRecord)),
            "audit": session.scalar(select(func.count()).select_from(AuditLogRecord)),
            "accepted": session.scalar(select(func.count()).select_from(AcceptedInferenceRecord)),
            "models": session.scalar(select(func.count()).select_from(RegisteredModelRecord)),
        }
    for _ in range(10):
        assert (await client.get("/api/summary")).status_code == 200
    with app.state.session_factory() as session:
        after = {
            "findings": session.scalar(select(func.count()).select_from(FindingRecord)),
            "audit": session.scalar(select(func.count()).select_from(AuditLogRecord)),
            "accepted": session.scalar(select(func.count()).select_from(AcceptedInferenceRecord)),
            "models": session.scalar(select(func.count()).select_from(RegisteredModelRecord)),
        }
    assert after == before


@pytest.mark.anyio
async def test_summary_survives_database_reopen(client, test_settings):
    await ingest(client, dataset_finding(), model_finding())
    expected = (await client.get("/api/summary")).json()
    reopened = create_app(test_settings)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=reopened), base_url="http://reopened") as new_client:
        actual = (await new_client.get("/api/summary")).json()
    expected.pop("generated_at")
    actual.pop("generated_at")
    assert actual == expected

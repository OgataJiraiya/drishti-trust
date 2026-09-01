from collections import defaultdict
from collections import Counter
import math
from typing import Any


SEVERITY_WEIGHTS = {
    "LOW": 10.0,
    "MEDIUM": 30.0,
    "HIGH": 60.0,
    "CRITICAL": 100.0,
}


def _finding_event_key(finding: dict[str, Any]) -> tuple[str, str, tuple[str, ...]]:
    """Identify the underlying event represented by a finding.

    Category, affected asset, and detector evidence distinguish genuine
    separate events while collapsing repeated copies of the same finding.
    """

    category = str(finding.get("category", ""))
    asset_id = str(finding.get("asset_id", ""))
    evidence = tuple(sorted(str(item) for item in finding.get("evidence", [])))
    return category, asset_id, evidence


def _finding_contribution(finding: dict[str, Any]) -> float:
    severity = finding.get("severity")
    if not isinstance(severity, str) or severity.upper() not in SEVERITY_WEIGHTS:
        raise ValueError(f"Unknown finding severity: {severity!r}")

    confidence = finding.get("confidence", 0.0)
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        raise ValueError("Finding confidence must be a finite number from 0 to 1.")
    if not math.isfinite(confidence) or not 0.0 <= confidence <= 1.0:
        raise ValueError("Finding confidence must be a finite number from 0 to 1.")

    return SEVERITY_WEIGHTS[severity.upper()] * float(confidence)


def _dataset_severity(risk_score: float) -> str:
    if risk_score == 0:
        return "NONE"
    if risk_score < 20:
        return "LOW"
    if risk_score < 50:
        return "MEDIUM"
    if risk_score < 75:
        return "HIGH"
    return "CRITICAL"


def aggregate_dataset_risk(
    findings: list[dict[str, Any]],
) -> dict[str, Any]:
    """Create a deterministic, explainable dataset-risk heuristic.

    Scores are severity weights multiplied by confidence. Repeated records of
    the same category/asset/evidence event contribute only their highest score,
    then total risk is capped at 100. This is an application-level review
    heuristic, not a scientifically validated probability of compromise.
    """

    findings_by_category = Counter(
        str(finding.get("category", "UNKNOWN")) for finding in findings
    )
    findings_by_severity = Counter(
        str(finding.get("severity", "UNKNOWN")) for finding in findings
    )

    contributions_by_event: dict[tuple[str, str, tuple[str, ...]], float] = {}
    for finding in findings:
        event_key = _finding_event_key(finding)
        contribution = _finding_contribution(finding)
        contributions_by_event[event_key] = max(
            contributions_by_event.get(event_key, 0.0),
            contribution,
        )

    raw_score = sum(contributions_by_event.values())
    risk_score = round(min(100.0, raw_score), 2)
    unique_finding_count = len(contributions_by_event)
    duplicate_finding_count = len(findings) - unique_finding_count

    return {
        "risk_score": risk_score,
        "severity": _dataset_severity(risk_score),
        "finding_count": len(findings),
        "unique_finding_count": unique_finding_count,
        "deduplicated_finding_count": duplicate_finding_count,
        "findings_by_category": dict(sorted(findings_by_category.items())),
        "findings_by_severity": dict(sorted(findings_by_severity.items())),
        "explanation": (
            "Dataset risk is a capped severity-weighted confidence heuristic "
            "(LOW=10, MEDIUM=30, HIGH=60, CRITICAL=100). Repeated records "
            "for the same category, asset, and evidence count once at their "
            "highest contribution; it is a review signal, not proof of compromise."
        ),
    }


def aggregate_contributor_risk(
    findings: list[dict[str, Any]],
    samples: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    Aggregate suspicious findings by contributor and batch.

    Returns simple contributor-level risk summaries.
    """

    sample_lookup = {
        sample["sample_id"]: sample
        for sample in samples
    }

    contributor_findings = defaultdict(list)

    for finding in findings:
        sample = sample_lookup.get(finding["asset_id"])

        if sample is None:
            continue

        contributor_id = sample.get("contributor_id")
        batch_id = sample.get("batch_id")

        if contributor_id is None:
            continue

        contributor_findings[contributor_id].append(
            {
                "finding": finding,
                "batch_id": batch_id,
            }
        )

    results = []

    for contributor_id, entries in contributor_findings.items():
        findings_count = len(entries)

        average_confidence = round(
            sum(
                entry["finding"]["confidence"]
                for entry in entries
            )
            / findings_count,
            2,
        )

        batches = sorted(
            {
                entry["batch_id"]
                for entry in entries
                if entry["batch_id"] is not None
            }
        )

        results.append(
            {
                "contributor_id": contributor_id,
                "finding_count": findings_count,
                "average_confidence": average_confidence,
                "affected_batches": batches,
            }
        )

    return sorted(
        results,
        key=lambda item: item["average_confidence"],
        reverse=True,
    )

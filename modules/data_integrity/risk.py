from collections import defaultdict
from typing import Any


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
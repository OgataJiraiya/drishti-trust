from collections import Counter
from typing import Any
from .bounds import (
    DEFAULT_MAX_EVIDENCE_ITEMS,
    DEFAULT_MAX_EVIDENCE_LENGTH,
    DEFAULT_MAX_IDENTIFIER_LENGTH,
    bounded_evidence,
    bounded_text,
    stable_finding_id,
)


def find_label_anomalies(
    samples: list[dict[str, Any]],
    min_count: int = 2,
) -> list[dict[str, Any]]:
    """
    Find labels that occur fewer than min_count times.

    This is a simple frequency-based heuristic.
    Rare labels are flagged for review; rarity does not prove
    that a label is incorrect or malicious.
    """

    label_counts = Counter()

    for sample in samples:
        for label in sample.get("labels", []):
            label_counts[label] += 1

    anomalies = []

    for label, count in sorted(label_counts.items()):
        if count < min_count:
            anomalies.append(
                {
                    "label": label,
                    "count": count,
                }
            )

    return anomalies


def create_label_anomaly_findings(
    anomalies: list[dict[str, Any]],
    *,
    max_evidence_items: int = DEFAULT_MAX_EVIDENCE_ITEMS,
    max_evidence_length: int = DEFAULT_MAX_EVIDENCE_LENGTH,
    max_identifier_length: int = DEFAULT_MAX_IDENTIFIER_LENGTH,
) -> list[dict[str, Any]]:
    """
    Convert label anomalies into Finding Schema v1 findings.
    """

    findings = []

    for anomaly in anomalies:
        label = anomaly["label"]
        count = anomaly["count"]
        raw_asset_id = f"label:{label}"
        asset_id, asset_truncated = bounded_text(raw_asset_id, max_identifier_length)
        raw_evidence = [f"label={label}", f"label_count={count}"]
        evidence, evidence_truncated = bounded_evidence(
            raw_evidence, max_items=max_evidence_items, max_length=max_evidence_length
        )

        findings.append(
            {
                "finding_id": stable_finding_id(
                    "dataset_integrity", "LABEL_ANOMALY", raw_asset_id, raw_evidence
                ),
                "module": "dataset_integrity",
                "asset_type": "dataset",
                "asset_id": asset_id,
                "category": "LABEL_ANOMALY",
                "severity": "MEDIUM",
                "confidence": 0.75,
                "reason": (
                    f"Label '{bounded_text(label, max_identifier_length)[0]}' occurs only {count} time(s) "
                    "in the dataset."
                ),
                "evidence": evidence,
                "recommendation": "REVIEW",
                "limitations": [
                    "Rare labels may be legitimate and are not "
                    "evidence of poisoning by themselves."
                ],
            }
        )

    return findings

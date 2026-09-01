from collections import Counter
from typing import Any


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
) -> list[dict[str, Any]]:
    """
    Convert label anomalies into Finding Schema v1 findings.
    """

    findings = []

    for index, anomaly in enumerate(anomalies, start=1):
        label = anomaly["label"]
        count = anomaly["count"]

        findings.append(
            {
                "finding_id": f"F-DATA-{index:03d}",
                "module": "dataset_integrity",
                "asset_type": "dataset",
                "asset_id": f"label:{label}",
                "category": "LABEL_ANOMALY",
                "severity": "MEDIUM",
                "confidence": 0.75,
                "reason": (
                    f"Label '{label}' occurs only {count} time(s) "
                    "in the dataset."
                ),
                "evidence": [
                    f"label={label}",
                    f"label_count={count}",
                ],
                "recommendation": "REVIEW",
                "limitations": [
                    "Rare labels may be legitimate and are not "
                    "evidence of poisoning by themselves."
                ],
            }
        )

    return findings
"""Central, deterministic Milestone-11 assurance policy constants and helpers."""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from dataclasses import dataclass
from enum import StrEnum

from backend.schemas.common import FindingModule, Recommendation, Severity


class AssuranceStatus(StrEnum):
    GOOD = "GOOD"
    WATCH = "WATCH"
    REVIEW = "REVIEW"
    HIGH_RISK = "HIGH_RISK"
    UNKNOWN = "UNKNOWN"


SEVERITY_PENALTIES: dict[Severity, Decimal] = {
    Severity.INFO: Decimal("2"),
    Severity.LOW: Decimal("8"),
    Severity.MEDIUM: Decimal("20"),
    Severity.HIGH: Decimal("45"),
    Severity.CRITICAL: Decimal("75"),
}

MODULE_WEIGHTS: dict[FindingModule, Decimal] = {
    FindingModule.DATASET_INTEGRITY: Decimal("0.25"),
    FindingModule.MODEL_INTEGRITY: Decimal("0.35"),
    FindingModule.INFERENCE_INTEGRITY: Decimal("0.25"),
    FindingModule.DISTRIBUTION_SHIFT: Decimal("0.15"),
}

MODULE_DISPLAY_NAMES: dict[FindingModule, str] = {
    FindingModule.DATASET_INTEGRITY: "Dataset Integrity",
    FindingModule.MODEL_INTEGRITY: "Model Integrity",
    FindingModule.INFERENCE_INTEGRITY: "Inference Integrity",
    FindingModule.DISTRIBUTION_SHIFT: "Distribution Health",
}

DISPOSITION_RANK: dict[Recommendation, int] = {
    Recommendation.ACCEPT: 0,
    Recommendation.REVIEW: 1,
    Recommendation.QUARANTINE: 2,
    Recommendation.REJECT: 3,
}

STATUS_DISPOSITION: dict[AssuranceStatus, Recommendation] = {
    AssuranceStatus.GOOD: Recommendation.ACCEPT,
    AssuranceStatus.WATCH: Recommendation.REVIEW,
    AssuranceStatus.REVIEW: Recommendation.REVIEW,
    AssuranceStatus.HIGH_RISK: Recommendation.QUARANTINE,
    AssuranceStatus.UNKNOWN: Recommendation.REVIEW,
}

@dataclass(frozen=True)
class CriticalOverridePolicy:
    asset_disposition: Recommendation
    module_disposition: Recommendation
    system_disposition: Recommendation


# Asset, module, and system effects are deliberately distinct. No current rule
# globally rejects the entire pipeline.
CRITICAL_CATEGORY_OVERRIDES: dict[str, CriticalOverridePolicy] = {
    "OUTPUT_TAMPERING": CriticalOverridePolicy(
        Recommendation.REJECT, Recommendation.QUARANTINE, Recommendation.QUARANTINE
    ),
    "REPLAY_DETECTED": CriticalOverridePolicy(
        Recommendation.REJECT, Recommendation.QUARANTINE, Recommendation.QUARANTINE
    ),
    "MODEL_SUBSTITUTION": CriticalOverridePolicy(
        Recommendation.QUARANTINE, Recommendation.QUARANTINE, Recommendation.QUARANTINE
    ),
    "BACKDOOR_BEHAVIOR": CriticalOverridePolicy(
        Recommendation.QUARANTINE, Recommendation.QUARANTINE, Recommendation.QUARANTINE
    ),
}

PIPELINE_CRITICAL_ASSET_TYPES = frozenset({"model", "pipeline", "dataset"})


def status_for_score(score: Decimal) -> AssuranceStatus:
    if score >= Decimal("90"):
        return AssuranceStatus.GOOD
    if score >= Decimal("70"):
        return AssuranceStatus.WATCH
    if score >= Decimal("40"):
        return AssuranceStatus.REVIEW
    return AssuranceStatus.HIGH_RISK


def strongest_disposition(*values: Recommendation) -> Recommendation:
    return max(values, key=lambda value: DISPOSITION_RANK[value])


def module_effect_for_recommendation(value: Recommendation) -> Recommendation:
    """Translate an asset recommendation into module-level pressure."""
    if value == Recommendation.REJECT:
        return Recommendation.REVIEW
    return value


def system_effect_for_finding(value: Recommendation, asset_type: str) -> Recommendation:
    """Translate an asset recommendation without treating asset REJECT as global REJECT."""
    if value == Recommendation.ACCEPT:
        return Recommendation.ACCEPT
    if value == Recommendation.QUARANTINE and asset_type in PIPELINE_CRITICAL_ASSET_TYPES:
        return Recommendation.QUARANTINE
    return Recommendation.REVIEW


def display_score(value: Decimal) -> float:
    return float(value.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


def display_calculation(value: Decimal) -> float:
    return float(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))

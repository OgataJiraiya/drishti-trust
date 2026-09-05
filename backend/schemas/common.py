"""Central shared values for the frozen team Finding Schema v1."""
from enum import StrEnum


class FindingModule(StrEnum):
    DATASET_INTEGRITY = "dataset_integrity"
    MODEL_INTEGRITY = "model_integrity"
    INFERENCE_INTEGRITY = "inference_integrity"
    DISTRIBUTION_SHIFT = "distribution_shift"


class Severity(StrEnum):
    INFO = "INFO"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class Recommendation(StrEnum):
    ACCEPT = "ACCEPT"
    REVIEW = "REVIEW"
    QUARANTINE = "QUARANTINE"
    REJECT = "REJECT"

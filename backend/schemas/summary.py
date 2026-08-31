"""Typed dashboard-ready assurance summary contracts."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from backend.core.assurance_policy import AssuranceStatus
from backend.schemas.common import FindingModule, Recommendation, Severity
from backend.schemas.evidence import Finding
from backend.schemas.inference import StrictSchema


class SeverityCounts(StrictSchema):
    INFO: int = 0
    LOW: int = 0
    MEDIUM: int = 0
    HIGH: int = 0
    CRITICAL: int = 0


class RecommendationCounts(StrictSchema):
    ACCEPT: int = 0
    REVIEW: int = 0
    QUARANTINE: int = 0
    REJECT: int = 0


class CategoryPenalty(StrictSchema):
    category: str
    source_finding_id: str
    severity: Severity
    confidence: float
    severity_penalty: float
    penalty: float


class ScoreExplanation(StrictSchema):
    starting_score: float
    category_penalties: list[CategoryPenalty]
    total_penalty: float
    formula: str


class ModuleAssurance(StrictSchema):
    display_name: str
    availability: Literal["ASSESSED", "UNKNOWN"]
    availability_reason: str
    assurance_score: float | None
    status: AssuranceStatus
    disposition: Recommendation
    finding_count: int
    severity_counts: SeverityCounts
    recommendation_counts: RecommendationCounts
    category_counts: dict[str, int]
    top_findings: list[Finding]
    score_explanation: ScoreExplanation | None


class CriticalOverrideDetail(StrictSchema):
    category: str
    asset_id: str
    asset_disposition: Recommendation
    system_effect: Recommendation


class OverallAssurance(StrictSchema):
    assurance_score: float | None
    score_status: Literal["UNAVAILABLE", "PROVISIONAL", "COMPLETE"]
    assessment_coverage: float
    disposition: Recommendation
    reason: str
    critical_overrides: list[str]
    most_concerning_module: FindingModule | None
    critical_override_details: list[CriticalOverrideDetail]


class AuditIntegritySummary(StrictSchema):
    status: Literal["VALID", "COMPROMISED", "UNAVAILABLE"]
    records_checked: int | None
    first_broken_audit_id: str | None = None


class FindingCounts(SeverityCounts):
    total: int = 0


class AssuranceSummary(StrictSchema):
    assessment_id: str | None = None
    assessment_status: Literal["DRAFT", "ACTIVE", "SEALED"] | None = None
    scope_mode: Literal["EXPLICIT_ASSESSMENT", "ACTIVE_ASSESSMENT", "GLOBAL_LEGACY"] = "GLOBAL_LEGACY"
    trust_scope: Literal["authenticated", "all"]
    trusted_finding_count: int
    excluded_untrusted_finding_count: int
    generated_at: datetime
    overall: OverallAssurance
    modules: dict[FindingModule, ModuleAssurance]
    audit_integrity: AuditIntegritySummary
    latest_findings: list[Finding]
    finding_counts: FindingCounts
    recommendation_counts: RecommendationCounts
    limitations: list[str]

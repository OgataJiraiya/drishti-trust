"""Read-only, explainable assurance aggregation over Finding Schema v1."""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy.orm import Session

from backend.core.assurance_policy import (
    CRITICAL_CATEGORY_OVERRIDES,
    MODULE_DISPLAY_NAMES,
    MODULE_WEIGHTS,
    SEVERITY_PENALTIES,
    STATUS_DISPOSITION,
    AssuranceStatus,
    display_calculation,
    display_score,
    module_effect_for_recommendation,
    status_for_score,
    strongest_disposition,
    system_effect_for_finding,
)
from backend.database.repository import (
    AssessmentMembershipRepository,
    EvidenceRepository,
    ModuleRunAuthenticationRepository,
)
from backend.schemas.common import FindingModule, Recommendation, Severity
from backend.schemas.evidence import Finding
from backend.schemas.summary import (
    AssuranceSummary,
    AuditIntegritySummary,
    CategoryPenalty,
    CriticalOverrideDetail,
    FindingCounts,
    ModuleAssurance,
    OverallAssurance,
    RecommendationCounts,
    ScoreExplanation,
    SeverityCounts,
)
from backend.services.audit_service import AuditService
from backend.services.evidence_service import EvidenceService


class SummaryService:
    def build(
        self, session: Session, audit_service: AuditService, trust_scope: str = "authenticated",
        assessment_id: str | None = None, assessment_status: str | None = None,
        scope_mode: str = "GLOBAL_LEGACY",
    ) -> AssuranceSummary:
        records = EvidenceRepository(session).all_ingestion_order()
        if assessment_id is None:
            eligible_ids = {record.finding_id for record in records}
            authentication_repository = ModuleRunAuthenticationRepository(session)
            trusted_ids = authentication_repository.authenticated_finding_ids()
            completed_modules = authentication_repository.authenticated_modules()
        else:
            memberships = AssessmentMembershipRepository(session)
            eligible_ids = memberships.finding_ids(assessment_id)
            trusted_ids = memberships.finding_ids(assessment_id, "ED25519")
            completed_modules = memberships.authenticated_modules(assessment_id)
            records = [record for record in records if record.finding_id in eligible_ids]
        trusted_count = len(eligible_ids & trusted_ids)
        excluded_count = len(eligible_ids - trusted_ids)
        selected_records = (
            records if trust_scope == "all"
            else [record for record in records if record.finding_id in trusted_ids]
        )
        findings = [EvidenceService.to_schema(record) for record in selected_records]
        modules: dict[FindingModule, ModuleAssurance] = {}
        exact_scores: dict[FindingModule, Decimal] = {}
        assessed_weight = Decimal("0")
        critical_overrides: list[str] = []

        for module in FindingModule:
            module_findings = [finding for finding in findings if finding.module == module]
            summary, exact_score, overrides = self._module_summary(
                module, module_findings, module.value in completed_modules
            )
            modules[module] = summary
            if exact_score is not None:
                exact_scores[module] = exact_score
                assessed_weight += MODULE_WEIGHTS[module]
            for override in overrides:
                if override not in critical_overrides:
                    critical_overrides.append(override)

        audit = self._audit_summary(session, audit_service)
        override_details: list[CriticalOverrideDetail] = []
        for finding in findings:
            if finding.severity == Severity.CRITICAL and finding.category in CRITICAL_CATEGORY_OVERRIDES:
                policy = CRITICAL_CATEGORY_OVERRIDES[finding.category]
                override_details.append(CriticalOverrideDetail(
                    category=finding.category,
                    asset_id=finding.asset_id,
                    asset_disposition=policy.asset_disposition,
                    system_effect=policy.system_disposition,
                ))
        coverage = assessed_weight
        if not exact_scores:
            overall_score = None
            score_status = "UNAVAILABLE"
            disposition = Recommendation.REVIEW
        else:
            weighted_sum = sum(
                (exact_scores[module] * MODULE_WEIGHTS[module] for module in exact_scores),
                Decimal("0"),
            )
            exact_overall = weighted_sum / assessed_weight
            overall_score = display_score(exact_overall)
            score_status = "COMPLETE" if coverage == Decimal("1") else "PROVISIONAL"
            disposition = STATUS_DISPOSITION[status_for_score(exact_overall)]
            disposition = strongest_disposition(disposition, *(modules[module].disposition for module in exact_scores))
            for finding in findings:
                disposition = strongest_disposition(
                    disposition,
                    system_effect_for_finding(finding.recommendation, finding.asset_type),
                )
            for category in critical_overrides:
                disposition = strongest_disposition(
                    disposition, CRITICAL_CATEGORY_OVERRIDES[category].system_disposition
                )
        if audit.status == "COMPROMISED":
            disposition = strongest_disposition(disposition, Recommendation.QUARANTINE)
            critical_overrides.append("AUDIT_CHAIN_COMPROMISED")
        elif audit.status == "UNAVAILABLE":
            disposition = strongest_disposition(disposition, Recommendation.REVIEW)
        if coverage < Decimal("1"):
            disposition = strongest_disposition(disposition, Recommendation.REVIEW)
        reason = (
            "No assurance evidence has been ingested."
            if not exact_scores and audit.status == "VALID"
            else self._overall_reason(disposition, coverage, critical_overrides, audit.status)
        )

        severity_counts = self._severity_counts(findings)
        recommendation_counts = self._recommendation_counts(findings)
        limitations: list[str] = []
        for finding in findings:
            for limitation in finding.limitations:
                if limitation not in limitations and len(limitations) < 20:
                    limitations.append(limitation)

        return AssuranceSummary(
            assessment_id=assessment_id,
            assessment_status=assessment_status,
            scope_mode=scope_mode,
            trust_scope=trust_scope,
            trusted_finding_count=trusted_count,
            excluded_untrusted_finding_count=excluded_count,
            generated_at=datetime.now(timezone.utc),
            overall=OverallAssurance(
                assurance_score=overall_score,
                score_status=score_status,
                assessment_coverage=float(coverage),
                disposition=disposition,
                reason=reason,
                critical_overrides=critical_overrides,
                most_concerning_module=(
                    min(exact_scores, key=lambda module: (exact_scores[module], list(FindingModule).index(module)))
                    if exact_scores else None
                ),
                critical_override_details=override_details,
            ),
            modules=modules,
            audit_integrity=audit,
            latest_findings=list(reversed(findings))[:10],
            finding_counts=FindingCounts(total=len(findings), **severity_counts.model_dump()),
            recommendation_counts=recommendation_counts,
            limitations=limitations,
        )

    def _module_summary(
        self, module: FindingModule, findings: list[Finding], completed: bool = False
    ) -> tuple[ModuleAssurance, Decimal | None, list[str]]:
        if not findings:
            return ModuleAssurance(
                display_name=MODULE_DISPLAY_NAMES[module],
                availability="UNKNOWN",
                availability_reason=(
                    "Authenticated module completion reported zero findings; absence of findings "
                    "does not establish safety."
                    if completed else
                    "No assessment evidence has been ingested for this module."
                ),
                assurance_score=None,
                status=AssuranceStatus.UNKNOWN,
                disposition=Recommendation.REVIEW,
                finding_count=0,
                severity_counts=SeverityCounts(),
                recommendation_counts=RecommendationCounts(),
                category_counts={},
                top_findings=[],
                score_explanation=None,
            ), None, []

        category_sources: dict[str, tuple[Decimal, Finding]] = {}
        overrides: list[str] = []
        disposition = Recommendation.ACCEPT
        for finding in findings:
            penalty = SEVERITY_PENALTIES[finding.severity] * Decimal(str(finding.confidence))
            existing = category_sources.get(finding.category)
            if existing is None or penalty > existing[0] or (penalty == existing[0] and finding.finding_id < existing[1].finding_id):
                category_sources[finding.category] = (penalty, finding)
            disposition = strongest_disposition(
                disposition, module_effect_for_recommendation(finding.recommendation)
            )
            if finding.severity == Severity.CRITICAL and finding.category in CRITICAL_CATEGORY_OVERRIDES:
                disposition = strongest_disposition(
                    disposition, CRITICAL_CATEGORY_OVERRIDES[finding.category].module_disposition
                )
                if finding.category not in overrides:
                    overrides.append(finding.category)

        raw_total = sum((penalty for penalty, _finding in category_sources.values()), Decimal("0"))
        total_penalty = min(Decimal("100"), raw_total)
        exact_score = max(Decimal("0"), min(Decimal("100"), Decimal("100") - total_penalty))
        status = status_for_score(exact_score)
        disposition = strongest_disposition(disposition, STATUS_DISPOSITION[status])
        category_penalties = [
            CategoryPenalty(
                category=category,
                source_finding_id=finding.finding_id,
                severity=finding.severity,
                confidence=finding.confidence,
                severity_penalty=float(SEVERITY_PENALTIES[finding.severity]),
                penalty=display_calculation(penalty),
            )
            for category, (penalty, finding) in sorted(category_sources.items())
        ]
        top_findings = sorted(
            findings,
            key=lambda finding: (-SEVERITY_PENALTIES[finding.severity], -Decimal(str(finding.confidence)), finding.finding_id),
        )[:5]
        return ModuleAssurance(
            display_name=MODULE_DISPLAY_NAMES[module],
            availability="ASSESSED",
            availability_reason=f"{len(findings)} finding(s) ingested for this module.",
            assurance_score=display_score(exact_score),
            status=status,
            disposition=disposition,
            finding_count=len(findings),
            severity_counts=self._severity_counts(findings),
            recommendation_counts=self._recommendation_counts(findings),
            category_counts=dict(sorted(Counter(finding.category for finding in findings).items())),
            top_findings=top_findings,
            score_explanation=ScoreExplanation(
                starting_score=100.0,
                category_penalties=category_penalties,
                total_penalty=display_calculation(total_penalty),
                formula="score = 100 - min(100, sum(max(severity_penalty * confidence) per category))",
            ),
        ), exact_score, overrides

    @staticmethod
    def _severity_counts(findings: list[Finding]) -> SeverityCounts:
        counts = Counter(finding.severity.value for finding in findings)
        return SeverityCounts(**{severity.value: counts[severity.value] for severity in Severity})

    @staticmethod
    def _recommendation_counts(findings: list[Finding]) -> RecommendationCounts:
        counts = Counter(finding.recommendation.value for finding in findings)
        return RecommendationCounts(**{value.value: counts[value.value] for value in Recommendation})

    @staticmethod
    def _audit_summary(session: Session, audit_service: AuditService) -> AuditIntegritySummary:
        try:
            verification = audit_service.verify(session)
            return AuditIntegritySummary(
                status=verification.status,
                records_checked=verification.records_checked,
                first_broken_audit_id=verification.first_broken_audit_id,
            )
        except Exception:
            # The caller owns the transaction. In particular, sealing holds a
            # write reservation that must survive an unavailable audit read.
            return AuditIntegritySummary(status="UNAVAILABLE", records_checked=None, first_broken_audit_id=None)

    @staticmethod
    def _overall_reason(disposition: Recommendation, coverage: Decimal,
                        overrides: list[str], audit_status: str) -> str:
        if audit_status == "COMPROMISED":
            return "Audit-chain compromise requires quarantine regardless of the numerical assurance score."
        if overrides:
            return f"Critical override(s) {', '.join(overrides)} and module recommendations require {disposition.value}."
        if audit_status == "UNAVAILABLE":
            return "Audit integrity is unavailable; the result cannot be accepted without review."
        if coverage < Decimal("1"):
            return "Assessment coverage is incomplete; the normalized score is provisional and requires review."
        return f"Complete evidence coverage and score-band/module recommendations produce {disposition.value}."

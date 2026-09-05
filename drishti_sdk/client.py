"""Small synchronous client for the existing authenticated integration protocol."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pydantic import ValidationError as PydanticValidationError

from backend.core.canonical import canonical_json_bytes
from backend.core.hashing import sha256_bytes
from backend.core.signing import load_private_key
from backend.integrations.signing import sign_module_run
from backend.schemas.evidence import Finding
from backend.schemas.integration import ModuleRunSubmission
from .errors import AssessmentStateError, AuthenticationError, ConflictError, DrishtiError, ValidationError
from .ids import deterministic_finding_id


class DrishtiClient:
    def __init__(self, base_url: str = "http://127.0.0.1:8000", timeout: float = 10.0,
                 admin_token: str | None = None, transport: httpx.BaseTransport | None = None) -> None:
        headers = {"Authorization": f"Bearer {admin_token}"} if admin_token else None
        self._http = httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout,
                                  headers=headers, transport=transport)

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "DrishtiClient": return self
    def __exit__(self, *_: object) -> None: self.close()

    def health(self) -> dict[str, Any]: return self._request("GET", "/health")
    def get_current_assessment(self) -> dict[str, Any]:
        return self._request("GET", "/api/assessments/current")
    def get_assessment(self, assessment_id: str) -> dict[str, Any]:
        return self._request("GET", f"/api/assessments/{assessment_id}")
    def get_run(self, run_id: str) -> dict[str, Any]:
        return self._request("GET", f"/api/integration/runs/{run_id}")
    def get_summary(self, assessment_id: str | None = None,
                    trust_scope: str = "authenticated") -> dict[str, Any]:
        if trust_scope not in {"authenticated", "all"}: raise ValidationError("invalid trust_scope")
        params = {"trust_scope": trust_scope}
        if assessment_id: params["assessment_id"] = assessment_id
        return self._request("GET", "/api/summary", params=params)

    def build_finding(self, *, module: str, asset_type: str, asset_id: str,
                      category: str, severity: str, confidence: float, reason: str,
                      evidence: list[str], recommendation: str,
                      limitations: list[str] | None = None, finding_id: str | None = None,
                      stable_evidence_key: object | None = None) -> Finding:
        if finding_id is None:
            if stable_evidence_key is None:
                raise ValidationError("finding_id or stable_evidence_key is required")
            try: finding_id = deterministic_finding_id(module, asset_id, category, stable_evidence_key)
            except (TypeError, ValueError) as exc: raise ValidationError(str(exc)) from exc
        try:
            return Finding(finding_id=finding_id, module=module, asset_type=asset_type,
                asset_id=asset_id, category=category, severity=severity, confidence=confidence,
                reason=reason, evidence=evidence, recommendation=recommendation,
                limitations=limitations or [])
        except PydanticValidationError as exc:
            raise ValidationError(self._validation_detail(exc)) from exc

    def build_run(self, *, module: str, assessment_id: str, producer: str,
                  producer_version: str | None, findings: list[Finding | dict[str, Any]],
                  run_id: str | None = None) -> ModuleRunSubmission:
        if not assessment_id or not assessment_id.strip():
            raise ValidationError("assessment_id is required for the preferred scoped workflow")
        normalized = [item if isinstance(item, Finding) else self._finding(item) for item in findings]
        if run_id is None:
            identity = {"assessment_id": assessment_id, "module": module, "producer": producer,
                        "producer_version": producer_version,
                        "finding_ids": [item.finding_id for item in normalized]}
            run_id = f"RUN-{sha256_bytes(canonical_json_bytes(identity))[:24]}"
        try:
            return ModuleRunSubmission(run_id=run_id, assessment_id=assessment_id, module=module,
                producer=producer, producer_version=producer_version, findings=normalized)
        except PydanticValidationError as exc:
            raise ValidationError(self._validation_detail(exc)) from exc

    def sign_run(self, run: ModuleRunSubmission | dict[str, Any], *,
                 private_key: Ed25519PrivateKey | None = None,
                 private_key_path: str | Path | None = None) -> str:
        validated = self._run(run)
        if (private_key is None) == (private_key_path is None):
            raise ValidationError("provide exactly one private_key or private_key_path")
        try:
            key = private_key or load_private_key(Path(private_key_path))  # type: ignore[arg-type]
        except (OSError, TypeError, ValueError) as exc:
            raise ValidationError("unable to load Ed25519 private key") from exc
        return sign_module_run(validated, key)

    def submit_signed_run(self, *, run: ModuleRunSubmission | dict[str, Any], key_id: str,
                          private_key: Ed25519PrivateKey | None = None,
                          private_key_path: str | Path | None = None) -> dict[str, Any]:
        validated = self._run(run)
        signature = self.sign_run(validated, private_key=private_key,
                                  private_key_path=private_key_path)
        return self._request("POST", "/api/integration/signed-runs", json={
            "run": validated.model_dump(mode="json"), "key_id": key_id, "signature": signature})

    # Thin admin/demo helpers. They preserve, rather than hide, the bearer trust boundary.
    def create_assessment(self, assessment_id: str, name: str, **extra: Any) -> dict[str, Any]:
        return self._request("POST", "/api/assessments", json={"assessment_id": assessment_id,
            "name": name, "description": extra.get("description"), "metadata": extra.get("metadata", {})})
    def activate_assessment(self, assessment_id: str) -> dict[str, Any]:
        return self._request("POST", f"/api/assessments/{assessment_id}/activate")
    def seal_assessment(self, assessment_id: str) -> dict[str, Any]:
        return self._request("POST", f"/api/assessments/{assessment_id}/seal")
    def verify_snapshot(self, assessment_id: str) -> dict[str, Any]:
        return self._request("GET", f"/api/assessments/{assessment_id}/snapshot/verify")
    def register_producer(self, producer_id: str, display_name: str, module: str) -> dict[str, Any]:
        return self._request("POST", "/api/producers", json={"producer_id": producer_id,
            "display_name": display_name, "module": module, "metadata": {"purpose": "local-demo"}})
    def register_producer_key(self, producer_id: str, key_id: str, public_key_pem: str) -> dict[str, Any]:
        return self._request("POST", f"/api/producers/{producer_id}/keys",
                             json={"key_id": key_id, "public_key_pem": public_key_pem})
    def drain_outbox(self) -> dict[str, Any]: return self._request("POST", "/api/audit/outbox/drain")
    def outbox_status(self) -> dict[str, Any]: return self._request("GET", "/api/audit/outbox/status")
    def verify_audit(self) -> dict[str, Any]: return self._request("GET", "/api/audit/verify")
    def create_checkpoint(self) -> dict[str, Any]: return self._request("POST", "/api/audit/checkpoints")
    def verify_checkpoint(self, bundle: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/api/audit/checkpoints/verify", json=bundle)

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        try: response = self._http.request(method, path, **kwargs)
        except httpx.HTTPError as exc: raise DrishtiError("DRISHTI backend is unavailable") from exc
        if response.is_success:
            data = response.json()
            return data if isinstance(data, dict) else {"data": data}
        detail = "DRISHTI request failed"
        try:
            candidate = response.json().get("detail")
            if isinstance(candidate, str): detail = candidate[:512]
            elif isinstance(candidate, list): detail = "request validation failed"
        except (ValueError, AttributeError): pass
        cls: type[DrishtiError]
        if response.status_code in {401, 403}: cls = AuthenticationError
        elif response.status_code == 422: cls = ValidationError
        elif response.status_code == 409 and "assessment" in detail.lower(): cls = AssessmentStateError
        elif response.status_code == 409: cls = ConflictError
        else: cls = DrishtiError
        raise cls(detail, response.status_code)

    @staticmethod
    def _validation_detail(exc: PydanticValidationError) -> str:
        errors = exc.errors()
        return str(errors[0].get("msg", "local validation failed"))[:512] if errors else "local validation failed"
    def _finding(self, value: dict[str, Any]) -> Finding:
        try: return Finding.model_validate(value)
        except PydanticValidationError as exc: raise ValidationError(self._validation_detail(exc)) from exc
    def _run(self, value: ModuleRunSubmission | dict[str, Any]) -> ModuleRunSubmission:
        if isinstance(value, ModuleRunSubmission): return value
        try: return ModuleRunSubmission.model_validate(value)
        except PydanticValidationError as exc: raise ValidationError(self._validation_detail(exc)) from exc

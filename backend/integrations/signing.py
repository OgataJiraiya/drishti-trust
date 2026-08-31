"""Module-author helper using the backend's exact canonical signing rules."""
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from backend.core.canonical import canonical_json_bytes
from backend.core.signing import sign_bytes
from backend.schemas.integration import ModuleRunSubmission


def module_run_signing_bytes(run: ModuleRunSubmission) -> bytes:
    """Return the exact bytes authenticated by the signed-run endpoint."""
    payload = run.model_dump(mode="json")
    # Preserve pre-M14 canonical bytes for legacy runs while binding the field
    # whenever an assessment is explicitly supplied.
    if payload["assessment_id"] is None:
        del payload["assessment_id"]
    return canonical_json_bytes(payload)


def sign_module_run(run: ModuleRunSubmission, private_key: Ed25519PrivateKey) -> str:
    """Return a base64 Ed25519 signature without persisting private material."""
    return sign_bytes(private_key, module_run_signing_bytes(run))

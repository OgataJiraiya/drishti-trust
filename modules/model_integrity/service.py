"""M1 orchestration: safe artifact identity plus optional ONNX structure."""
from dataclasses import replace
from pathlib import Path

from .artifacts import inspect_artifact
from .errors import OnnxInspectionError, ResourceLimitError
from .models import Fingerprints, InspectionLevel, ModelManifest
from .onnx_inspector import inspect_onnx

DEFAULT_MAX_ONNX_PARSE_BYTES = 512 * 1024 * 1024


class ModelIntegrityService:
    def __init__(self, max_onnx_parse_bytes: int = DEFAULT_MAX_ONNX_PARSE_BYTES) -> None:
        if max_onnx_parse_bytes <= 0:
            raise ValueError("max_onnx_parse_bytes must be positive")
        self.max_onnx_parse_bytes = max_onnx_parse_bytes

    def inspect(self, path: Path | str, *, strict: bool = False,
                approved_operators: set[str] | None = None) -> ModelManifest:
        candidate = Path(path)
        artifact = inspect_artifact(candidate, strict=strict)
        structure = None; structural = None; parameters = None; issues = []
        limitations: list[str] = []
        if artifact.claimed_format == "onnx":
            if artifact.byte_size > self.max_onnx_parse_bytes:
                if strict:
                    raise ResourceLimitError(
                        "ONNX artifact exceeds the configured structural parse-size limit"
                    )
                artifact = replace(artifact, inspection_level=InspectionLevel.ARTIFACT_ONLY)
                limitations.append("STRUCTURE_UNAVAILABLE_RESOURCE_LIMIT")
                return ModelManifest(schema_version="1", artifact=artifact, structure=None,
                    fingerprints=Fingerprints(artifact_sha256=artifact.sha256,
                        structural_sha256=None, parameter_metadata_sha256=None),
                    issues=[], limitations=limitations)
            try:
                structure, structural, parameters, issues, limitations = inspect_onnx(
                    candidate, approved_operators)
            except OnnxInspectionError:
                if strict: raise
                raise
        elif artifact.inspection_level == InspectionLevel.ARTIFACT_ONLY:
            limitations.append("STRUCTURE_UNAVAILABLE: format is not safely parsed in M1")
        else:
            limitations.append("STRUCTURE_UNAVAILABLE: unsupported or unknown format")
        return ModelManifest(schema_version="1", artifact=artifact, structure=structure,
            fingerprints=Fingerprints(artifact_sha256=artifact.sha256,
                structural_sha256=structural, parameter_metadata_sha256=parameters),
            issues=issues, limitations=limitations)

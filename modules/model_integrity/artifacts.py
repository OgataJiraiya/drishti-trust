"""Streaming identity and conservative artifact validation."""
from __future__ import annotations
import hashlib
from pathlib import Path

from .errors import InvalidArtifactError, UnsupportedFormatError
from .models import ArtifactReport, InspectionLevel

FORMATS = {
    ".onnx": ("onnx", InspectionLevel.STRUCTURAL),
    ".pt": ("pytorch", InspectionLevel.ARTIFACT_ONLY),
    ".pth": ("pytorch", InspectionLevel.ARTIFACT_ONLY),
    ".ckpt": ("checkpoint", InspectionLevel.ARTIFACT_ONLY),
    ".h5": ("hdf5", InspectionLevel.ARTIFACT_ONLY),
    ".keras": ("keras", InspectionLevel.ARTIFACT_ONLY),
    ".tflite": ("tflite", InspectionLevel.ARTIFACT_ONLY),
    ".pb": ("protobuf", InspectionLevel.ARTIFACT_ONLY),
}


def validate_file(path: Path | str) -> Path:
    candidate = Path(path)
    try:
        if candidate.is_symlink(): raise InvalidArtifactError("symbolic-link model paths are rejected")
        if not candidate.exists(): raise InvalidArtifactError("model artifact does not exist")
        if not candidate.is_file(): raise InvalidArtifactError("model artifact must be a regular file")
        size = candidate.stat().st_size
    except OSError as exc:
        raise InvalidArtifactError("model artifact metadata is not accessible") from exc
    if size == 0: raise InvalidArtifactError("model artifact is empty")
    return candidate


def sha256_file(path: Path | str, chunk_size: int = 1024 * 1024) -> str:
    candidate = validate_file(path)
    digest = hashlib.sha256()
    try:
        with candidate.open("rb") as stream:
            for chunk in iter(lambda: stream.read(chunk_size), b""): digest.update(chunk)
    except OSError as exc:
        raise InvalidArtifactError("model artifact could not be read") from exc
    return digest.hexdigest()


def inspect_artifact(path: Path | str, strict: bool = False) -> ArtifactReport:
    candidate = validate_file(path)
    extension = candidate.suffix.lower()
    claimed, level = FORMATS.get(extension, ("unknown", InspectionLevel.UNSUPPORTED))
    if strict and level != InspectionLevel.STRUCTURAL:
        raise UnsupportedFormatError(f"strict structural inspection is unsupported for {claimed}")
    digest = sha256_file(candidate)
    return ArtifactReport(artifact_id=f"model:sha256:{digest}", filename=candidate.name,
        extension=extension, sha256=digest, byte_size=candidate.stat().st_size,
        claimed_format=claimed, inspection_level=level)

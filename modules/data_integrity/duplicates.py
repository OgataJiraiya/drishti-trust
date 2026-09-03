import hashlib
from typing import Any
from .bounds import (
    DEFAULT_MAX_DUPLICATE_PATHS,
    DEFAULT_MAX_EVIDENCE_ITEMS,
    DEFAULT_MAX_EVIDENCE_LENGTH,
    DEFAULT_MAX_PATH_LENGTH,
    bounded_evidence,
    bounded_text,
    stable_finding_id,
)
from .safe_images import SUPPORTED_IMAGE_EXTENSIONS, safe_image_files


def calculate_sha256(file_path: str) -> str:
    """Calculate the SHA-256 hash of a file."""

    sha256 = hashlib.sha256()

    with open(file_path, "rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            sha256.update(chunk)

    return sha256.hexdigest()


def find_exact_duplicates(images_dir: str, *, dataset_root: str | None = None, max_images: int | None = None, max_file_size: int | None = None) -> dict[str, list[str]]:
    """
    Find exact duplicate images using SHA-256.

    Returns:
        Dictionary mapping each duplicate SHA-256 hash
        to the list of matching image paths.
    """

    hashes: dict[str, list[str]] = {}
    from pathlib import Path
    images_path = Path(images_dir)
    options = {"dataset_root": dataset_root, "max_images": max_images}
    if max_file_size is not None: options["max_file_size"] = max_file_size
    image_files = safe_image_files(images_path, **options)

    for image_file in image_files:
        file_hash = calculate_sha256(str(image_file))
        relative_path = image_file.relative_to(images_path).as_posix()

        hashes.setdefault(file_hash, []).append(relative_path)

    return {
        file_hash: paths
        for file_hash, paths in hashes.items()
        if len(paths) > 1
    }


def create_duplicate_findings(
    duplicates: dict[str, list[str]],
    *,
    max_evidence_items: int = DEFAULT_MAX_EVIDENCE_ITEMS,
    max_evidence_length: int = DEFAULT_MAX_EVIDENCE_LENGTH,
    max_path_length: int = DEFAULT_MAX_PATH_LENGTH,
    max_duplicate_paths: int = DEFAULT_MAX_DUPLICATE_PATHS,
) -> list[dict[str, Any]]:
    """
    Convert exact duplicate groups into Finding Schema v1 findings.
    """

    findings = []

    for file_hash, paths in sorted(duplicates.items()):
        paths = sorted(paths)
        identity = [file_hash, *paths]
        shown = paths[:max_duplicate_paths]
        bounded_paths = []
        paths_truncated = len(paths) > max_duplicate_paths
        for path in shown:
            bounded, was_truncated = bounded_text(path, max_path_length)
            bounded_paths.append(bounded)
            paths_truncated = paths_truncated or was_truncated
        evidence, evidence_truncated = bounded_evidence(
            [
                f"sha256={file_hash}",
                f"duplicate_count={len(paths)}",
                f"duplicate_paths={','.join(bounded_paths)}"
                + (f";truncated={len(paths)-len(shown)}" if len(paths) > len(shown) else ""),
            ],
            max_items=max_evidence_items,
            max_length=max_evidence_length,
        )
        truncated = paths_truncated or evidence_truncated
        asset_id, _ = bounded_text(paths[0], max_path_length)
        findings.append(
            {
                "finding_id": stable_finding_id(
                    "dataset_integrity", "EXACT_DUPLICATE", paths[0], identity
                ),
                "module": "dataset_integrity",
                "asset_type": "sample",
                "asset_id": asset_id,
                "category": "EXACT_DUPLICATE",
                "severity": "HIGH",
                "confidence": 1.0,
                "reason": (
                    f"Sample is byte-for-byte identical to "
                    f"{len(paths) - 1} other sample(s)."
                ),
                "evidence": evidence,
                "recommendation": "REVIEW",
                "limitations": [
                    "SHA-256 detects byte-identical files only."
                ],
                "truncated": truncated,
            }
        )

    return findings

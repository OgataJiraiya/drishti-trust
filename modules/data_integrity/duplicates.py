import hashlib
from pathlib import Path
from typing import Any


SUPPORTED_IMAGE_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".bmp",
    ".tif",
    ".tiff",
    ".webp",
}


def calculate_sha256(file_path: str) -> str:
    """Calculate the SHA-256 hash of a file."""

    sha256 = hashlib.sha256()

    with open(file_path, "rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            sha256.update(chunk)

    return sha256.hexdigest()


def find_exact_duplicates(images_dir: str) -> dict[str, list[str]]:
    """
    Find exact duplicate images using SHA-256.

    Returns:
        Dictionary mapping each duplicate SHA-256 hash
        to the list of matching image paths.
    """

    images_path = Path(images_dir)

    if not images_path.is_dir():
        raise ValueError(f"Images directory does not exist: {images_dir}")

    hashes: dict[str, list[str]] = {}

    image_files = sorted(
        file_path
        for file_path in images_path.rglob("*")
        if file_path.is_file()
        and file_path.suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS
    )

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
) -> list[dict[str, Any]]:
    """
    Convert exact duplicate groups into Finding Schema v1 findings.
    """

    findings = []

    for index, (file_hash, paths) in enumerate(duplicates.items(), start=1):
        findings.append(
            {
                "finding_id": f"F-DATA-{index:03d}",
                "module": "dataset_integrity",
                "asset_type": "sample",
                "asset_id": paths[0],
                "category": "EXACT_DUPLICATE",
                "severity": "HIGH",
                "confidence": 1.0,
                "reason": (
                    f"Sample is byte-for-byte identical to "
                    f"{len(paths) - 1} other sample(s)."
                ),
                "evidence": [
                    f"sha256={file_hash}",
                    f"duplicate_count={len(paths)}",
                    f"duplicate_paths={','.join(paths)}",
                ],
                "recommendation": "REVIEW",
                "limitations": [
                    "SHA-256 detects byte-identical files only."
                ],
            }
        )

    return findings
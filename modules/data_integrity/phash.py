from typing import Any

import numpy as np
from PIL import Image

from .bounds import (
    DEFAULT_MAX_EVIDENCE_ITEMS,
    DEFAULT_MAX_EVIDENCE_LENGTH,
    DEFAULT_MAX_PATH_LENGTH,
    bounded_evidence,
    bounded_text,
    stable_finding_id,
)
from .safe_images import safe_image_files


def calculate_phash(file_path: str) -> str:
    """Calculate the perceptual hash of an image."""

    with Image.open(file_path) as image:
        pixels = np.asarray(image.convert("L").resize((32, 32), Image.Resampling.LANCZOS),
                            dtype=np.float64)
    coordinates = np.arange(32, dtype=np.float64)
    frequencies = np.arange(8, dtype=np.float64)[:, None]
    basis = np.cos((np.pi / 32) * (coordinates + 0.5) * frequencies)
    coefficients = basis @ pixels @ basis.T
    values = coefficients[:8, :8].ravel()
    bits = values > np.median(values[1:])
    return f"{sum(int(bit) << (63 - index) for index, bit in enumerate(bits)):016x}"


def phash_distance(hash_a: str, hash_b: str) -> int:
    """Calculate Hamming distance between two pHashes."""

    return bin(int(hash_a, 16) ^ int(hash_b, 16)).count("1")


def find_near_duplicates(
    images_dir: str,
    threshold: int = 10,
    *, dataset_root: str | None = None, max_images: int | None = None,
    max_pair_comparisons: int | None = None, max_matches: int | None = None,
    return_metadata: bool = False,
) -> list[dict[str, Any]] | dict[str, Any]:
    """
    Find visually similar images using pHash.

    Images with a Hamming distance less than or equal
    to the threshold are returned.
    """

    from pathlib import Path
    images_path = Path(images_dir)
    image_files = safe_image_files(images_path, dataset_root, max_images=max_images)

    hashes = {}

    for image_file in image_files:
        relative_path = image_file.relative_to(images_path).as_posix()
        hashes[relative_path] = calculate_phash(str(image_file))

    matches = []

    paths = list(hashes)

    comparisons = 0
    for index, first_path in enumerate(paths):
        for second_path in paths[index + 1:]:
            if max_pair_comparisons is not None and comparisons >= max_pair_comparisons:
                return {"matches": matches, "partial": True, "reason": "max_pair_comparisons reached."} if return_metadata else matches
            comparisons += 1
            distance = phash_distance(
                hashes[first_path],
                hashes[second_path],
            )

            if distance <= threshold:
                matches.append(
                    {
                        "image_a": first_path,
                        "image_b": second_path,
                        "phash_distance": distance,
                    }
                )
                if max_matches is not None and len(matches) >= max_matches:
                    return {"matches": matches, "partial": True, "reason": "max_matches reached."} if return_metadata else matches

    return {"matches": matches, "partial": False} if return_metadata else matches


def phash_confidence(
    distance: int,
    hash_size: int = 8,
) -> float:
    """
    Convert pHash Hamming distance into a similarity confidence.

    A distance of 0 means identical perceptual hashes.
    The score decreases linearly as the distance increases.
    """

    max_distance = hash_size * hash_size

    if distance < 0:
        raise ValueError("Distance cannot be negative.")

    if distance >= max_distance:
        return 0.0

    return round(1.0 - (distance / max_distance), 2)


def create_near_duplicate_findings(
    matches: list[dict[str, Any]],
    *,
    max_evidence_items: int = DEFAULT_MAX_EVIDENCE_ITEMS,
    max_evidence_length: int = DEFAULT_MAX_EVIDENCE_LENGTH,
    max_path_length: int = DEFAULT_MAX_PATH_LENGTH,
) -> list[dict[str, Any]]:
    """
    Convert pHash matches into Finding Schema v1 findings.
    """

    findings = []

    for match in sorted(matches, key=lambda item: (item["image_a"], item["image_b"])):
        distance = match["phash_distance"]
        raw_asset_id = match["image_a"]
        raw_match = match["image_b"]
        asset_id, asset_truncated = bounded_text(raw_asset_id, max_path_length)
        matched_sample, match_truncated = bounded_text(raw_match, max_path_length)
        raw_evidence = [f"phash_distance={distance}", f"matched_sample={raw_match}"]
        evidence, evidence_truncated = bounded_evidence(
            raw_evidence, max_items=max_evidence_items, max_length=max_evidence_length
        )
        confidence = phash_confidence(distance)

        if confidence >= 0.85:
            severity = "HIGH"
        elif confidence >= 0.70:
            severity = "MEDIUM"
        else:
            severity = "LOW"

        findings.append(
            {
                "finding_id": stable_finding_id(
                    "dataset_integrity", "NEAR_DUPLICATE", raw_asset_id, raw_evidence
                ),
                "module": "dataset_integrity",
                "asset_type": "sample",
                "asset_id": asset_id,
                "category": "NEAR_DUPLICATE",
                "severity": severity,
                "confidence": confidence,
                "reason": (
                    f"Sample is perceptually similar to {matched_sample}."
                ),
                "evidence": evidence,
                "recommendation": "REVIEW",
                "limitations": [
                    "Similarity heuristic; visually similar legitimate "
                    "images may be flagged."
                ],
            }
        )

    return findings

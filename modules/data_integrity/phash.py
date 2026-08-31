from pathlib import Path
from typing import Any

import imagehash
from PIL import Image


SUPPORTED_IMAGE_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".bmp",
    ".tif",
    ".tiff",
    ".webp",
}


def calculate_phash(file_path: str) -> str:
    """Calculate the perceptual hash of an image."""

    with Image.open(file_path) as image:
        return str(imagehash.phash(image))


def phash_distance(hash_a: str, hash_b: str) -> int:
    """Calculate Hamming distance between two pHashes."""

    return bin(int(hash_a, 16) ^ int(hash_b, 16)).count("1")


def find_near_duplicates(
    images_dir: str,
    threshold: int = 10,
) -> list[dict[str, Any]]:
    """
    Find visually similar images using pHash.

    Images with a Hamming distance less than or equal
    to the threshold are returned.
    """

    images_path = Path(images_dir)

    if not images_path.is_dir():
        raise ValueError(f"Images directory does not exist: {images_dir}")

    image_files = sorted(
        file_path
        for file_path in images_path.rglob("*")
        if file_path.is_file()
        and file_path.suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS
    )

    hashes = {}

    for image_file in image_files:
        relative_path = image_file.relative_to(images_path).as_posix()
        hashes[relative_path] = calculate_phash(str(image_file))

    matches = []

    paths = list(hashes)

    for index, first_path in enumerate(paths):
        for second_path in paths[index + 1:]:
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

    return matches


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
) -> list[dict[str, Any]]:
    """
    Convert pHash matches into Finding Schema v1 findings.
    """

    findings = []

    for index, match in enumerate(matches, start=1):
        distance = match["phash_distance"]
        confidence = phash_confidence(distance)

        if confidence >= 0.85:
            severity = "HIGH"
        elif confidence >= 0.70:
            severity = "MEDIUM"
        else:
            severity = "LOW"

        findings.append(
            {
                "finding_id": f"F-DATA-{index:03d}",
                "module": "dataset_integrity",
                "asset_type": "sample",
                "asset_id": match["image_a"],
                "category": "NEAR_DUPLICATE",
                "severity": severity,
                "confidence": confidence,
                "reason": (
                    f"Sample is perceptually similar to "
                    f"{match['image_b']}."
                ),
                "evidence": [
                    f"phash_distance={distance}",
                    f"matched_sample={match['image_b']}",
                ],
                "recommendation": "REVIEW",
                "limitations": [
                    "Similarity heuristic; visually similar legitimate "
                    "images may be flagged."
                ],
            }
        )

    return findings
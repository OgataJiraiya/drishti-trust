"""Unified, dataset-agnostic orchestration for data-integrity checks."""

from collections import Counter
import hashlib
from itertools import combinations
from pathlib import Path
from typing import Any

from .duplicates import (
    SUPPORTED_IMAGE_EXTENSIONS,
    create_duplicate_findings,
    find_exact_duplicates,
)
from .ingestion import DatasetIngestionError, load_coco, load_yolo
from .label_anomaly import (
    create_label_anomaly_findings,
    find_label_anomalies,
)
from .ood import analyze_ood, create_ood_findings
from .phash import create_near_duplicate_findings, find_near_duplicates
from .risk import aggregate_contributor_risk, aggregate_dataset_risk
from .safe_images import SafeImageError, safe_image_files
from .bounds import (
    DEFAULT_MAX_DUPLICATE_PATHS,
    DEFAULT_MAX_EVIDENCE_ITEMS,
    DEFAULT_MAX_EVIDENCE_LENGTH,
    DEFAULT_MAX_IDENTIFIER_LENGTH,
    DEFAULT_MAX_PATH_LENGTH,
)


def _image_count(directory: Path) -> int:
    return len(safe_image_files(directory))


def _discover_images_directory(
    dataset_path: Path,
    images_dir: str | None,
) -> Path:
    if images_dir is not None:
        path = Path(images_dir)
        if not path.is_dir():
            raise ValueError(f"Images directory does not exist: {images_dir}")
        return path

    images_root = dataset_path / "images"
    if images_root.is_dir():
        train_directory = images_root / "train"
        if train_directory.is_dir():
            return train_directory
        if _image_count(images_root):
            return images_root

    return dataset_path


def _discover_labels_directory(
    dataset_path: Path,
    images_path: Path,
    labels_dir: str | None,
) -> Path | None:
    if labels_dir is not None:
        path = Path(labels_dir)
        return path if path.is_dir() else None

    try:
        relative_images_path = images_path.relative_to(dataset_path)
    except ValueError:
        return None

    if relative_images_path.parts and relative_images_path.parts[0] == "images":
        candidate = dataset_path / "labels" / Path(
            *relative_images_path.parts[1:]
        )
        if candidate.is_dir():
            return candidate

    return None


def _discover_classes_file(
    dataset_path: Path,
    classes_file: str | None,
) -> Path | None:
    if classes_file is not None:
        path = Path(classes_file)
        return path if path.is_file() else None

    for candidate in (
        dataset_path / "classes.txt",
        dataset_path / "labels" / "classes.txt",
    ):
        if candidate.is_file():
            return candidate

    return None


def _stable_findings(findings: list[dict[str, Any]], max_findings: int | None) -> list[dict[str, Any]]:
    """Assign identity-derived IDs so detector order cannot renumber findings."""
    result = []
    for finding in findings:
        identity = "|".join([str(finding.get("module")), str(finding.get("category")), str(finding.get("asset_id")), *sorted(map(str, finding.get("evidence", [])))])
        identifier = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16].upper()
        result.append({**finding, "finding_id": f"F-DATA-{identifier}"})
    result.sort(key=lambda item: item["finding_id"])
    return result if max_findings is None else result[:max_findings]


def analyze_dataset(
    dataset_path: str,
    *,
    images_dir: str | None = None,
    labels_dir: str | None = None,
    classes_file: str | None = None,
    annotation_file: str | None = None,
    run_duplicates: bool = True,
    run_phash: bool = True,
    run_labels: bool = True,
    run_ood: bool = True,
    run_risk: bool = True,
    phash_threshold: int = 10,
    label_min_count: int = 2,
    ood_calibration: str = "auto",
    ood_threshold: float | None = None,
    max_images: int | None = 10000,
    max_pair_comparisons: int | None = 1_000_000,
    max_matches: int | None = 10000,
    max_findings: int | None = 10000,
    max_file_size: int | None = None,
    ood_min_calibration_samples: int = 5,
    require_labels: bool = False,
    max_evidence_items: int = DEFAULT_MAX_EVIDENCE_ITEMS,
    max_evidence_length: int = DEFAULT_MAX_EVIDENCE_LENGTH,
    max_path_length: int = DEFAULT_MAX_PATH_LENGTH,
    max_identifier_length: int = DEFAULT_MAX_IDENTIFIER_LENGTH,
    max_duplicate_paths: int = DEFAULT_MAX_DUPLICATE_PATHS,
) -> dict[str, Any]:
    """Run supported integrity checks and return one Finding Schema v1 result.

    Optional detector data or runtime failures are reported in the result.
    Invalid dataset paths and unexpected programming errors still raise.
    """

    root = Path(dataset_path)
    if not root.is_dir():
        raise ValueError(f"Dataset path does not exist: {dataset_path}")

    image_path = _discover_images_directory(root, images_dir)
    if not image_path.is_dir():
        raise ValueError(f"Images directory does not exist: {image_path}")

    skipped_detectors: dict[str, str] = {}
    detector_errors: dict[str, str] = {}
    findings: list[dict[str, Any]] = []
    exact_duplicates: dict[str, list[str]] = {}
    samples: list[dict[str, Any]] = []
    ood_calibration_metadata: dict[str, Any] | None = None
    partial = False
    resource_limits_reached: dict[str, str] = {}
    resource_limits = {"max_images": max_images, "max_pair_comparisons": max_pair_comparisons, "max_matches": max_matches, "max_findings": max_findings}
    try:
        all_images = safe_image_files(image_path, root, **({"max_file_size": max_file_size} if max_file_size is not None else {}))
        if max_images is not None and len(all_images) > max_images:
            partial = True
            resource_limits_reached["max_images"] = "Image processing limited by max_images."
            skipped_detectors["image_limit"] = resource_limits_reached["max_images"]
    except SafeImageError as exc:
        raise ValueError(str(exc)) from exc

    discovered_labels = _discover_labels_directory(root, image_path, labels_dir)
    discovered_classes = _discover_classes_file(root, classes_file)

    if annotation_file is not None:
        try:
            samples = load_coco(annotation_file, dataset_root=root, max_images=max_images)
        except DatasetIngestionError as exc:
            detector_errors["ingestion"] = str(exc)
    elif discovered_labels is not None and discovered_classes is not None:
        try:
            samples = load_yolo(
                str(image_path),
                str(discovered_labels),
                str(discovered_classes),
                require_labels=require_labels,
                max_images=max_images,
                dataset_root=root,
            )
        except DatasetIngestionError as exc:
            detector_errors["ingestion"] = str(exc)
    elif run_labels:
        skipped_detectors["labels"] = (
            "YOLO labels and a classes file are required for label analysis."
        )

    if run_duplicates:
        try:
            duplicate_options = {"dataset_root": str(root), "max_images": max_images}
            if max_file_size is not None:
                duplicate_options["max_file_size"] = max_file_size
            exact_duplicates = find_exact_duplicates(str(image_path), **duplicate_options)
            findings.extend(create_duplicate_findings(
                exact_duplicates,
                max_evidence_items=max_evidence_items,
                max_evidence_length=max_evidence_length,
                max_path_length=max_path_length,
                max_duplicate_paths=max_duplicate_paths,
            ))
        except (OSError, ValueError) as exc:
            detector_errors["duplicates"] = str(exc)
    else:
        skipped_detectors["duplicates"] = "Disabled by configuration."

    if run_phash:
        try:
            match_result = find_near_duplicates(str(image_path), phash_threshold, dataset_root=str(root), max_images=max_images, max_pair_comparisons=max_pair_comparisons, max_matches=max_matches, return_metadata=True)
            if isinstance(match_result, list):
                matches = match_result
                match_result = {"partial": False}
            else:
                matches = match_result["matches"]
            if match_result.get("partial"):
                partial = True
                resource_limits_reached["phash"] = match_result["reason"]
                skipped_detectors["phash"] = match_result["reason"]
            findings.extend(create_near_duplicate_findings(
                matches,
                max_evidence_items=max_evidence_items,
                max_evidence_length=max_evidence_length,
                max_path_length=max_path_length,
            ))
        except (OSError, ValueError) as exc:
            detector_errors["phash"] = str(exc)
    else:
        skipped_detectors["phash"] = "Disabled by configuration."

    if run_labels:
        if samples:
            findings.extend(create_label_anomaly_findings(
                find_label_anomalies(samples, label_min_count),
                max_evidence_items=max_evidence_items,
                max_evidence_length=max_evidence_length,
                max_identifier_length=max_identifier_length,
            ))
        elif "labels" not in skipped_detectors:
            skipped_detectors["labels"] = (
                "No usable label samples were loaded."
            )
    else:
        skipped_detectors["labels"] = "Disabled by configuration."

    if run_ood:
        try:
            ood_result = analyze_ood(str(image_path), threshold=ood_threshold, calibration=ood_calibration,
                                     dataset_root=str(root), max_images=max_images,
                                     min_calibration_samples=ood_min_calibration_samples)
            ood_calibration_metadata = ood_result["calibration"]
            if ood_result.get("unavailable"):
                skipped_detectors["ood"] = ood_result["reason"]
            elif ood_calibration_metadata and ood_calibration_metadata.get("method") == "insufficient_samples":
                partial = True
                skipped_detectors["ood"] = ood_calibration_metadata["reason"]
                resource_limits_reached["ood_calibration"] = ood_calibration_metadata["reason"]
            findings.extend(create_ood_findings(
                ood_result["outliers"], ood_calibration_metadata,
                max_evidence_items=max_evidence_items,
                max_evidence_length=max_evidence_length,
                max_path_length=max_path_length,
            ))
        except (OSError, RuntimeError, ValueError) as exc:
            detector_errors["ood"] = str(exc)
    else:
        skipped_detectors["ood"] = "Disabled by configuration."

    # Exact byte duplicates are a stronger statement than their pHash=0 pair.
    exact_pairs = {tuple(sorted(pair)) for paths in exact_duplicates.values() for pair in combinations(paths, 2)}
    findings = [finding for finding in findings if not (finding["category"] == "NEAR_DUPLICATE" and tuple(sorted((finding["asset_id"], next((item.split("=", 1)[1] for item in finding["evidence"] if item.startswith("matched_sample=")), "")))) in exact_pairs)]
    unbounded_count = len(findings)
    findings = _stable_findings(findings, max_findings)
    if len(findings) != unbounded_count:
        partial = True
        resource_limits_reached["max_findings"] = "Findings limited by max_findings."
        skipped_detectors["findings_limit"] = resource_limits_reached["max_findings"]
    detector_risk_summary = aggregate_dataset_risk(findings)

    if run_risk:
        risk_summary = aggregate_contributor_risk(
            findings, samples, max_identifier_length=max_identifier_length
        )
        if not risk_summary:
            skipped_detectors["risk"] = (
                "No findings with contributor metadata were available."
            )
    else:
        risk_summary = []
        skipped_detectors["risk"] = "Disabled by configuration."

    return {
        "dataset_path": str(root),
        "image_directory": str(image_path),
        "image_count": _image_count(image_path),
        "findings": findings,
        "finding_count": len(findings),
        "findings_by_category": dict(
            sorted(Counter(finding["category"] for finding in findings).items())
        ),
        "findings_by_severity": dict(
            sorted(Counter(finding["severity"] for finding in findings).items())
        ),
        "detector_risk_summary": detector_risk_summary,
        "ood_calibration": ood_calibration_metadata,
        "risk_summary": risk_summary,
        "skipped_detectors": skipped_detectors,
        "detector_errors": detector_errors,
        "partial": partial,
        "resource_limits_reached": resource_limits_reached,
        "resource_limits": resource_limits,
    }

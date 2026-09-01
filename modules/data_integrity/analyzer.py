"""Unified, dataset-agnostic orchestration for data-integrity checks."""

from collections import Counter
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
from .risk import aggregate_contributor_risk


def _image_count(directory: Path) -> int:
    return sum(
        1
        for path in directory.rglob("*")
        if path.is_file()
        and path.suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS
    )


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


def _renumber_findings(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Give combined findings stable, unique Finding Schema v1 IDs."""

    numbered = []
    for index, finding in enumerate(findings, start=1):
        numbered.append({**finding, "finding_id": f"F-DATA-{index:03d}"})
    return numbered


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
    samples: list[dict[str, Any]] = []
    ood_calibration_metadata: dict[str, Any] | None = None

    discovered_labels = _discover_labels_directory(root, image_path, labels_dir)
    discovered_classes = _discover_classes_file(root, classes_file)

    if annotation_file is not None:
        try:
            samples = load_coco(annotation_file)
        except DatasetIngestionError as exc:
            detector_errors["ingestion"] = str(exc)
    elif discovered_labels is not None and discovered_classes is not None:
        try:
            samples = load_yolo(
                str(image_path),
                str(discovered_labels),
                str(discovered_classes),
            )
        except DatasetIngestionError as exc:
            detector_errors["ingestion"] = str(exc)
    elif run_labels:
        skipped_detectors["labels"] = (
            "YOLO labels and a classes file are required for label analysis."
        )

    if run_duplicates:
        try:
            findings.extend(create_duplicate_findings(
                find_exact_duplicates(str(image_path))
            ))
        except (OSError, ValueError) as exc:
            detector_errors["duplicates"] = str(exc)
    else:
        skipped_detectors["duplicates"] = "Disabled by configuration."

    if run_phash:
        try:
            findings.extend(create_near_duplicate_findings(
                find_near_duplicates(str(image_path), phash_threshold)
            ))
        except (OSError, ValueError) as exc:
            detector_errors["phash"] = str(exc)
    else:
        skipped_detectors["phash"] = "Disabled by configuration."

    if run_labels:
        if samples:
            findings.extend(create_label_anomaly_findings(
                find_label_anomalies(samples, label_min_count)
            ))
        elif "labels" not in skipped_detectors:
            skipped_detectors["labels"] = (
                "No usable label samples were loaded."
            )
    else:
        skipped_detectors["labels"] = "Disabled by configuration."

    if run_ood:
        try:
            ood_result = analyze_ood(
                str(image_path),
                threshold=ood_threshold,
                calibration=ood_calibration,
            )
            ood_calibration_metadata = ood_result["calibration"]
            findings.extend(create_ood_findings(ood_result["outliers"]))
        except (OSError, RuntimeError, ValueError) as exc:
            detector_errors["ood"] = str(exc)
    else:
        skipped_detectors["ood"] = "Disabled by configuration."

    findings = _renumber_findings(findings)

    if run_risk:
        risk_summary = aggregate_contributor_risk(findings, samples)
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
        "ood_calibration": ood_calibration_metadata,
        "risk_summary": risk_summary,
        "skipped_detectors": skipped_detectors,
        "detector_errors": detector_errors,
    }

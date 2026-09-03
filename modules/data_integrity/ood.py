from typing import Any
import os

import numpy as np
import torch
from PIL import Image
# These must be present before importing transformers/huggingface_hub, whose
# offline configuration is read at import time in some supported versions.
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
from transformers import CLIPModel, CLIPProcessor
from .bounds import (
    DEFAULT_MAX_EVIDENCE_ITEMS,
    DEFAULT_MAX_EVIDENCE_LENGTH,
    DEFAULT_MAX_PATH_LENGTH,
    bounded_evidence,
    bounded_text,
    stable_finding_id,
)
from .safe_images import safe_image_files

MIN_CALIBRATION_SAMPLES = 5
MAD_SCALE_FACTOR = 1.4826
DEFAULT_MAD_MULTIPLIER = 3.5
DEFAULT_PERCENTILE = 95.0


def calibrate_ood_threshold(
    scores: dict[str, float] | list[float] | np.ndarray,
    mad_multiplier: float = DEFAULT_MAD_MULTIPLIER,
    percentile: float = DEFAULT_PERCENTILE,
) -> tuple[float | None, dict[str, Any]]:
    """Derive a dataset-relative OOD threshold from observed scores.

    For datasets with enough observations, this uses the median plus a
    scaled MAD (median absolute deviation). The P95 score is retained as a
    lower bound so a nearly uniform score distribution does not produce an
    overly permissive threshold. Tiny datasets and zero-MAD distributions use
    a percentile fallback because robust spread estimates are not meaningful.

    This is an unsupervised review threshold, not evidence that a flagged
    image is poisoned or invalid.
    """

    if isinstance(scores, dict):
        values = list(scores.values())
    else:
        values = list(scores)

    score_array = np.asarray(values, dtype=float)

    if score_array.size == 0:
        return None, {
            "method": "empty",
            "threshold": None,
            "sample_count": 0,
            "median": None,
            "mad": None,
            "p95": None,
        }

    if not np.all(np.isfinite(score_array)):
        raise ValueError("OOD scores must be finite numbers.")

    if mad_multiplier <= 0:
        raise ValueError("mad_multiplier must be greater than zero.")

    if not 0 < percentile <= 100:
        raise ValueError("percentile must be in the range (0, 100].")

    median = float(np.median(score_array))
    mad = float(np.median(np.abs(score_array - median)))
    p95 = float(np.percentile(score_array, percentile))
    sample_count = int(score_array.size)

    metadata: dict[str, Any] = {
        "threshold": None,
        "sample_count": sample_count,
        "median": round(median, 6),
        "mad": round(mad, 6),
        "p95": round(p95, 6),
        "mad_multiplier": mad_multiplier,
        "percentile": percentile,
    }

    if sample_count < MIN_CALIBRATION_SAMPLES:
        metadata.update({"method": "insufficient_samples", "reason": "Insufficient samples for reliable OOD calibration."})
        return None, metadata
    if mad == 0:
        metadata["method"] = "percentile_fallback"
        metadata["threshold"] = round(p95, 6)
        return metadata["threshold"], metadata

    robust_standard_deviation = MAD_SCALE_FACTOR * mad
    mad_threshold = median + mad_multiplier * robust_standard_deviation
    threshold = max(mad_threshold, p95)

    metadata.update(
        {
            "method": "median_mad",
            "robust_standard_deviation": round(
                robust_standard_deviation,
                6,
            ),
            "mad_threshold": round(mad_threshold, 6),
            "threshold": round(threshold, 6),
        }
    )

    return metadata["threshold"], metadata


def load_clip_model(
    model_name: str = "openai/clip-vit-base-patch32",
):
    """Load the pretrained CLIP image model and processor."""

    # Belt-and-suspenders offline policy: transformers is never allowed to
    # consult the Hub even when a dependency changes its fallback behavior.
    # Explicitly prevent transformers from contacting Hugging Face.
    processor = CLIPProcessor.from_pretrained(model_name, local_files_only=True)
    model = CLIPModel.from_pretrained(model_name, local_files_only=True)

    model.eval()

    return processor, model


def calculate_image_embedding(
    image_path: str,
    processor,
    model,
) -> np.ndarray:
    """Generate a normalized CLIP embedding for one image."""

    with Image.open(image_path) as image:
        image = image.convert("RGB")

    inputs = processor(images=image, return_tensors="pt")

    with torch.no_grad():
        vision_output = model.vision_model(
            pixel_values=inputs["pixel_values"]
        )

    embedding = vision_output.pooler_output

    embedding = model.visual_projection(embedding)

    embedding = embedding / embedding.norm(
        p=2,
        dim=-1,
        keepdim=True,
    )

    return embedding.detach().cpu().numpy()[0]


def calculate_ood_scores(
    embeddings: dict[str, np.ndarray],
) -> dict[str, float]:
    """
    Calculate each image's cosine distance from the dataset centroid.

    Higher values indicate that an image is farther from the
    dataset's normal visual distribution.
    """

    if not embeddings:
        return {}

    matrix = np.vstack(list(embeddings.values()))

    centroid = matrix.mean(axis=0)

    centroid_norm = np.linalg.norm(centroid)

    if centroid_norm == 0:
        raise ValueError("Dataset centroid has zero magnitude.")

    centroid = centroid / centroid_norm

    scores = {}

    for path, embedding in embeddings.items():
        embedding_norm = np.linalg.norm(embedding)

        if embedding_norm == 0:
            raise ValueError(
                f"Embedding has zero magnitude: {path}"
            )

        normalized = embedding / embedding_norm

        cosine_similarity = float(
            np.dot(normalized, centroid)
        )

        scores[path] = round(
            1.0 - cosine_similarity,
            6,
        )

    return scores


def analyze_ood(
    images_dir: str,
    threshold: float | None = None,
    calibration: str = "auto",
    processor=None,
    model=None,
    min_calibration_samples: int = MIN_CALIBRATION_SAMPLES,
    max_images: int | None = None,
    dataset_root: str | None = None,
) -> dict[str, Any]:
    """Analyze image embeddings and return OOD outliers with calibration.

    Higher distance means more visually unusual. The returned calibration is
    dataset-relative in automatic mode. This result is a review signal, not a
    claim that any image is poisoned or invalid.
    """

    if processor is None or model is None:
        try:
            processor, model = load_clip_model()
        except (OSError, RuntimeError, EnvironmentError) as exc:
            return {"outliers": [], "scores": {}, "calibration": None, "unavailable": True,
                    "reason": f"OOD_UNAVAILABLE: local CLIP model is unavailable ({exc})."}
    from pathlib import Path
    images_path = Path(images_dir)
    image_files = safe_image_files(images_path, dataset_root, max_images=max_images)

    embeddings = {}

    for image_file in image_files:
        relative_path = image_file.relative_to(
            images_path
        ).as_posix()

        embeddings[relative_path] = calculate_image_embedding(
            str(image_file),
            processor,
            model,
        )

    scores = calculate_ood_scores(embeddings)

    if calibration not in {"auto", "fixed"}:
        raise ValueError(
            "calibration must be either 'auto' or 'fixed'."
        )

    if calibration == "fixed":
        if threshold is None:
            raise ValueError(
                "A threshold is required when calibration='fixed'."
            )
        active_threshold = threshold
        calibration_metadata = {
            "method": "fixed",
            "threshold": threshold,
            "sample_count": len(scores),
        }
    else:
        active_threshold, calibration_metadata = calibrate_ood_threshold(scores)
        if len(scores) < min_calibration_samples:
            calibration_metadata.update({"method": "insufficient_samples", "threshold": None,
                                         "reason": "Insufficient samples for reliable OOD calibration.",
                                         "minimum_samples": min_calibration_samples})
            active_threshold = None

    if active_threshold is None:
        return {
            "outliers": [],
            "scores": scores,
            "calibration": calibration_metadata,
        }

    outliers = []

    for path, score in scores.items():
        if score >= active_threshold:
            outliers.append(
                {
                    "image": path,
                    "ood_score": score,
                }
            )

    return {
        "outliers": sorted(
            outliers,
            key=lambda item: item["ood_score"],
            reverse=True,
        ),
        "scores": scores,
        "calibration": calibration_metadata,
    }


def find_ood_outliers(
    images_dir: str,
    threshold: float | None = None,
    calibration: str = "auto",
    processor=None,
    model=None,
) -> list[dict[str, Any]]:
    """Return OOD outliers while preserving the original public API."""

    result = analyze_ood(
        images_dir,
        threshold=threshold,
        calibration=calibration,
        processor=processor,
        model=model,
    )

    return result["outliers"]


def create_ood_findings(
    outliers: list[dict[str, Any]],
    calibration: dict[str, Any] | None = None,
    *,
    max_evidence_items: int = DEFAULT_MAX_EVIDENCE_ITEMS,
    max_evidence_length: int = DEFAULT_MAX_EVIDENCE_LENGTH,
    max_path_length: int = DEFAULT_MAX_PATH_LENGTH,
) -> list[dict[str, Any]]:
    """Convert OOD results into Finding Schema v1 findings."""

    findings = []

    threshold = float((calibration or {}).get("threshold") or 0.0)
    support = int((calibration or {}).get("sample_count") or 0)
    for outlier in outliers:
        score = outlier["ood_score"]
        image_path = outlier["image"]
        asset_id, asset_truncated = bounded_text(image_path, max_path_length)

        # Explainable review-confidence heuristic, deliberately not probability.
        excess = max(0.0, score - threshold)
        confidence = round(min(1.0, excess / max(0.05, 1.0 - threshold) * min(1.0, support / 20)), 2)

        if confidence >= 0.85:
            severity = "HIGH"
        elif confidence >= 0.70:
            severity = "MEDIUM"
        else:
            severity = "LOW"

        raw_evidence = [f"ood_score={score}", f"calibrated_threshold={threshold}"]
        evidence, evidence_truncated = bounded_evidence(
            raw_evidence, max_items=max_evidence_items, max_length=max_evidence_length
        )
        findings.append(
            {
                "finding_id": stable_finding_id(
                    "dataset_integrity", "OOD_OUTLIER", image_path, raw_evidence
                ),
                "module": "dataset_integrity",
                "asset_type": "sample",
                "asset_id": asset_id,
                "category": "OOD_OUTLIER",
                "severity": severity,
                "confidence": confidence,
                "reason": (
                    "Image embedding is unusually far from "
                    "the dataset visual centroid."
                ),
                "evidence": evidence,
                "recommendation": "REVIEW",
                "limitations": [
                    "OOD score is a dataset-relative heuristic.",
                    "Unusual legitimate images may be flagged.",
                    "Threshold requires calibration for the target dataset.",
                    "Confidence is a deterministic heuristic, not a probability.",
                ],
                "truncated": asset_truncated or evidence_truncated,
            }
        )

    return findings

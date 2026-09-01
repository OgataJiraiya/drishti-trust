"""Deterministic, model-free D1 image feature extraction."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import warnings

import numpy as np
from PIL import Image, UnidentifiedImageError

from .intake import streaming_sha256
from .models import FailureCode, ProfileLimits


KNOWN_FORMATS = frozenset({"JPEG", "PNG", "BMP", "TIFF", "WEBP"})
KNOWN_MODES = {"1": "GRAYSCALE", "L": "GRAYSCALE", "I": "GRAYSCALE",
               "F": "GRAYSCALE", "RGB": "RGB", "RGBA": "RGBA"}


class SampleProcessingError(Exception):
    def __init__(self, code: FailureCode): super().__init__(code.value); self.code = code


@dataclass(frozen=True)
class ImageFeatures:
    content_sha256: str
    byte_size: int
    detected_format: str
    mode_group: str
    width: int
    height: int
    pixel_count: int
    aspect_ratio: float
    brightness: float
    contrast: float
    sharpness: float
    entropy: float
    saturation: float


def _finite(value: float) -> float:
    result = float(value)
    if not np.isfinite(result): raise SampleProcessingError(FailureCode.INVALID_IMAGE)
    return result


def extract_image_features(path: Path, limits: ProfileLimits) -> ImageFeatures:
    try: before = path.stat()
    except OSError as exc: raise SampleProcessingError(FailureCode.IO_ERROR) from exc
    if before.st_size > limits.max_file_bytes:
        raise SampleProcessingError(FailureCode.RESOURCE_LIMIT)
    try: digest = streaming_sha256(path, limits.hash_chunk_bytes)
    except OSError as exc: raise SampleProcessingError(FailureCode.IO_ERROR) from exc
    try:
        after_hash = path.stat()
        if (before.st_size, before.st_mtime_ns) != (after_hash.st_size, after_hash.st_mtime_ns):
            raise SampleProcessingError(FailureCode.FILE_CHANGED)
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(path) as image:
                width, height = image.size
                if width <= 0 or height <= 0 or width > limits.max_pixels_per_image // height:
                    raise SampleProcessingError(FailureCode.RESOURCE_LIMIT)
                if getattr(image, "n_frames", 1) != 1:
                    raise SampleProcessingError(FailureCode.UNSUPPORTED_MULTIFRAME)
                detected_format = image.format if image.format in KNOWN_FORMATS else "OTHER"
                mode_group = KNOWN_MODES.get(image.mode, "OTHER")
                if mode_group == "OTHER": raise SampleProcessingError(FailureCode.UNSUPPORTED_IMAGE_MODE)
                # Alpha is intentionally ignored, never composited.
                rgb = np.asarray(image.convert("RGB"), dtype=np.float64) / 255.0
        after_decode = path.stat()
        if (after_hash.st_size, after_hash.st_mtime_ns) != (after_decode.st_size, after_decode.st_mtime_ns):
            raise SampleProcessingError(FailureCode.FILE_CHANGED)
    except SampleProcessingError: raise
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, ValueError, SyntaxError) as exc:
        raise SampleProcessingError(FailureCode.INVALID_IMAGE) from exc
    luminance = .2126 * rgb[..., 0] + .7152 * rgb[..., 1] + .0722 * rgb[..., 2]
    brightness = _finite(np.mean(luminance)); contrast = _finite(np.std(luminance, ddof=0))
    differences = []
    if width > 1: differences.append(np.diff(luminance, axis=1).ravel())
    if height > 1: differences.append(np.diff(luminance, axis=0).ravel())
    sharpness = _finite(np.var(np.concatenate(differences), ddof=0)) if differences else 0.0
    counts, _ = np.histogram(luminance, bins=limits.histogram_bins, range=(0.0, 1.0))
    probabilities = counts[counts > 0].astype(np.float64) / pixel_count(width, height)
    entropy = _finite(-np.sum(probabilities * np.log2(probabilities)))
    maximum = np.max(rgb, axis=2); minimum = np.min(rgb, axis=2)
    saturation_values = np.divide(maximum - minimum, maximum,
        out=np.zeros_like(maximum), where=maximum > 0)
    saturation = _finite(np.mean(saturation_values))
    return ImageFeatures(digest, before.st_size, detected_format, mode_group, width, height,
        pixel_count(width, height), _finite(width / height), brightness, contrast,
        sharpness, entropy, saturation)


def pixel_count(width: int, height: int) -> int: return width * height


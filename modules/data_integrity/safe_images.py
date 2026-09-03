"""Safe, deterministic image discovery shared by integrity detectors."""

from pathlib import Path

SUPPORTED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"}
DEFAULT_MAX_FILE_SIZE = 100 * 1024 * 1024


class SafeImageError(ValueError):
    """An image path is outside the permitted dataset boundary or unsafe."""


def _under(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def safe_image_files(
    images_dir: str | Path,
    dataset_root: str | Path | None = None,
    *,
    max_file_size: int = DEFAULT_MAX_FILE_SIZE,
    max_images: int | None = None,
) -> list[Path]:
    """Return sorted regular images contained in *dataset_root*.

    Symlinks and files resolving outside the root are rejected rather than
    followed.  ``max_images`` is a deterministic processing bound.
    """
    directory = Path(images_dir)
    if not directory.is_dir():
        raise SafeImageError(f"Images directory does not exist: {images_dir}")
    root = Path(dataset_root) if dataset_root is not None else directory
    root = root.resolve(strict=True)
    if not _under(directory.resolve(strict=True), root):
        raise SafeImageError("Images directory resolves outside dataset root.")
    if max_file_size <= 0:
        raise ValueError("max_file_size must be greater than zero.")

    files: list[Path] = []
    for candidate in directory.rglob("*"):
        if candidate.is_symlink():
            raise SafeImageError(f"Symlinked image path is not allowed: {candidate}")
        if candidate.suffix.lower() not in SUPPORTED_IMAGE_EXTENSIONS:
            continue
        if not candidate.is_file():
            continue
        resolved = candidate.resolve(strict=True)
        if not _under(resolved, root) or not resolved.is_file():
            raise SafeImageError(f"Image resolves outside dataset root: {candidate}")
        if resolved.stat().st_size > max_file_size:
            raise SafeImageError(f"Image exceeds file-size limit: {candidate}")
        files.append(candidate)
    files.sort(key=lambda path: path.relative_to(directory).as_posix())
    return files if max_images is None else files[:max_images]


def safe_dataset_path(root: str | Path, supplied: str) -> Path:
    """Validate a dataset-relative COCO file_name without normalising abuse."""
    dataset_root = Path(root).resolve(strict=True)
    path = Path(supplied)
    # Path.is_absolute does not identify Windows drive paths on POSIX hosts.
    if path.is_absolute() or (len(supplied) >= 2 and supplied[1] == ":"):
        raise SafeImageError(f"Absolute image path is not allowed: {supplied}")
    if ".." in path.parts:
        raise SafeImageError(f"Image path traversal is not allowed: {supplied}")
    candidate = dataset_root / path
    if candidate.is_symlink():
        raise SafeImageError(f"Symlinked image path is not allowed: {supplied}")
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise SafeImageError(f"Referenced image does not exist: {supplied}") from exc
    if not _under(resolved, dataset_root) or not resolved.is_file():
        raise SafeImageError(f"Referenced image is unsafe or not a regular file: {supplied}")
    return resolved

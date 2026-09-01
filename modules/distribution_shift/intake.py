"""Shared safe directory enumeration and streaming sample identity."""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
import os

from .models import FailureCode, ProfileLimits, SampleFailure


SUPPORTED_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"})


@dataclass(frozen=True)
class EnumeratedWindow:
    root: Path
    files: tuple[Path, ...]
    failures: tuple[SampleFailure, ...]


def _display(path: Path, root: Path, maximum: int) -> str:
    try: value = path.relative_to(root).as_posix()
    except ValueError: value = "<outside-root>"
    value = "".join(character if character >= " " and character != "\x7f" else "?" for character in value)
    return value[:maximum]


def enumerate_image_window(root: Path | str, limits: ProfileLimits) -> EnumeratedWindow:
    candidate = Path(root)
    if not candidate.exists(): raise FileNotFoundError("window root does not exist")
    if candidate.is_symlink() or not candidate.is_dir(): raise NotADirectoryError("window root must be a real directory")
    resolved_root = candidate.resolve(strict=True)
    files: list[Path] = []; failures: list[SampleFailure] = []
    for current, directories, names in os.walk(candidate, topdown=True, followlinks=False):
        current_path = Path(current)
        kept_directories = []
        for name in sorted(directories):
            if name.startswith("."): continue
            path = current_path / name
            if path.is_symlink():
                failures.append(SampleFailure(FailureCode.SYMLINK_REJECTED,
                    _display(path, candidate, limits.max_relative_path_length)))
            else: kept_directories.append(name)
        directories[:] = kept_directories
        for name in sorted(names):
            if name.startswith("."): continue
            path = current_path / name
            if path.suffix.lower() not in SUPPORTED_EXTENSIONS: continue
            relative = _display(path, candidate, limits.max_relative_path_length)
            if path.is_symlink():
                failures.append(SampleFailure(FailureCode.SYMLINK_REJECTED, relative)); continue
            try:
                resolved = path.resolve(strict=True)
                if not resolved.is_relative_to(resolved_root):
                    failures.append(SampleFailure(FailureCode.PATH_ESCAPE_REJECTED, relative)); continue
                if not path.is_file(): continue
            except OSError:
                failures.append(SampleFailure(FailureCode.IO_ERROR, relative)); continue
            files.append(path)
    files.sort(key=lambda item: item.relative_to(candidate).as_posix())
    failures.sort(key=lambda item: (item.relative_path, item.code.value))
    return EnumeratedWindow(candidate, tuple(files), tuple(failures))


def streaming_sha256(path: Path, chunk_bytes: int = 1024 * 1024) -> str:
    if chunk_bytes <= 0: raise ValueError("chunk_bytes must be positive")
    digest = sha256()
    with path.open("rb") as source:
        while chunk := source.read(chunk_bytes): digest.update(chunk)
    return digest.hexdigest()

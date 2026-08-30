"""Offline provenance configuration."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    data_dir: Path = Path(__file__).resolve().parents[1] / "data"
    key_dir: Path = Path(__file__).resolve().parents[1] / "keys"
    max_input_bytes: int = 25 * 1024 * 1024
    max_receipt_age_seconds: int = 300
    max_future_skew_seconds: int = 30
    max_model_bytes: int = 1024 * 1024 * 1024

    @property
    def database_path(self) -> Path:
        return self.data_dir / "provenance.sqlite3"


settings = Settings()

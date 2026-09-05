"""Offline provenance configuration."""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    data_dir: Path = Path(__file__).resolve().parents[1] / "data"
    key_dir: Path = Path(__file__).resolve().parents[1] / "keys"
    max_input_bytes: int = 25 * 1024 * 1024
    max_receipt_age_seconds: int = 300
    max_future_skew_seconds: int = 30
    max_model_bytes: int = 1024 * 1024 * 1024
    admin_bearer_token: str | None = None
    internal_ingest_bearer_token: str | None = None
    allow_unsigned_ingestion: bool = False

    @property
    def database_path(self) -> Path:
        return self.data_dir / "provenance.sqlite3"


def _environment_settings() -> Settings:
    return Settings(
        data_dir=Path(os.environ["DRISHTI_DATA_DIR"]) if os.getenv("DRISHTI_DATA_DIR") else Settings.data_dir,
        key_dir=Path(os.environ["DRISHTI_KEY_DIR"]) if os.getenv("DRISHTI_KEY_DIR") else Settings.key_dir,
        admin_bearer_token=os.getenv("DRISHTI_ADMIN_BEARER_TOKEN") or None,
        internal_ingest_bearer_token=os.getenv("DRISHTI_INTERNAL_INGEST_BEARER_TOKEN") or None,
        allow_unsigned_ingestion=os.getenv(
            "DRISHTI_ALLOW_UNSIGNED_INGESTION", "false"
        ).strip().lower() in {"1", "true", "yes", "on"},
    )


settings = _environment_settings()

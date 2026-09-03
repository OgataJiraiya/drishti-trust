"""Shared bounds and stable identity helpers for external detector data."""

from typing import Any, Iterable

from drishti_sdk.ids import deterministic_finding_id

DEFAULT_MAX_EVIDENCE_ITEMS = 20
DEFAULT_MAX_EVIDENCE_LENGTH = 512
DEFAULT_MAX_PATH_LENGTH = 256
DEFAULT_MAX_IDENTIFIER_LENGTH = 256
DEFAULT_MAX_DUPLICATE_PATHS = 20


def bounded_text(value: Any, maximum: int) -> tuple[str, bool]:
    if maximum <= 0:
        raise ValueError("Text bounds must be greater than zero.")
    text = str(value)
    if len(text) <= maximum:
        return text, False
    suffix = "...[truncated]"
    if maximum <= len(suffix):
        return text[:maximum], True
    return text[: maximum - len(suffix)] + suffix, True


def bounded_evidence(
    evidence: Iterable[Any],
    *,
    max_items: int = DEFAULT_MAX_EVIDENCE_ITEMS,
    max_length: int = DEFAULT_MAX_EVIDENCE_LENGTH,
) -> tuple[list[str], bool]:
    if max_items <= 0 or max_length <= 0:
        raise ValueError("Evidence bounds must be greater than zero.")
    values = list(evidence)
    truncated = len(values) > max_items
    result: list[str] = []
    for value in values[:max_items]:
        bounded, was_truncated = bounded_text(value, max_length)
        truncated = truncated or was_truncated
        result.append(bounded)
    if truncated:
        marker, _ = bounded_text("evidence_truncated=true", max_length)
        if len(result) == max_items and result:
            result[-1] = marker
        else:
            result.append(marker)
    return result, truncated


def stable_finding_id(
    module: str,
    category: str,
    asset_id: Any,
    evidence_identity: Iterable[Any],
) -> str:
    return deterministic_finding_id(
        module, str(asset_id), category, sorted(str(item) for item in evidence_identity)
    )

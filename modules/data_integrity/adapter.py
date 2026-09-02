"""Integration-ready boundary for the Person-3 signed ModuleRun SDK.

The SDK is not present in this repository.  This adapter intentionally only
validates/forwards Finding Schema v1 data; it never creates signatures.
"""
from typing import Any, Protocol


class DatasetIntegrityAdapter(Protocol):
    """Local boundary for a future Person-3 ModuleRun integration.

    Person-3's concrete SDK interface is not available in this repository, so
    this protocol intentionally does not claim to be its signature.
    """

    def submit_findings(self, findings: list[dict[str, Any]]) -> Any: ...

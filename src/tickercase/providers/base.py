"""Provider identity and capability declarations.

Design reference: provider id / display name / capability tuple pattern from
komako-workshop/digital-oracle digital_oracle/providers/base.py
(commit a63e4c19a2f3313d54914c44666febaf5ffb9d6f, MIT). New code.
"""

from __future__ import annotations

from dataclasses import dataclass


class ProviderError(RuntimeError):
    def __init__(self, code: str, message: str, *, url: str | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.url = url


class ProviderParseError(ProviderError):
    """Response arrived but did not have the documented shape."""


@dataclass(frozen=True)
class ProviderInfo:
    provider_id: str
    display_name: str
    capabilities: tuple[str, ...]
    limitations: tuple[str, ...] = ()


class Provider:
    provider_id: str = ""
    display_name: str = ""
    capabilities: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()

    def describe(self) -> ProviderInfo:
        return ProviderInfo(self.provider_id, self.display_name, self.capabilities, self.limitations)

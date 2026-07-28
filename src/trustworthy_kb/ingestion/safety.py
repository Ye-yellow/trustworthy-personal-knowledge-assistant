"""Deterministic document safety signals without matched content."""

from __future__ import annotations

import re
from collections import Counter
from enum import StrEnum

from pydantic import Field

from trustworthy_kb.ingestion.types import IngestionValue


class SafetyCategory(StrEnum):
    SECRET_MATERIAL = "SECRET_MATERIAL"
    BIDI_CONTROL = "BIDI_CONTROL"
    ZERO_WIDTH_CONTROL = "ZERO_WIDTH_CONTROL"
    LONG_ENCODING = "LONG_ENCODING"
    INSTRUCTION_INJECTION = "INSTRUCTION_INJECTION"


class SafetySeverity(StrEnum):
    LOW = "LOW"
    HIGH = "HIGH"


class SafetySignal(IngestionValue):
    category: SafetyCategory
    severity: SafetySeverity
    count: int = Field(ge=1)


class SafetyReport(IngestionValue):
    signals: tuple[SafetySignal, ...]

    @property
    def requires_quarantine(self) -> bool:
        """Return whether at least one high-confidence signal was found."""

        return any(signal.severity is SafetySeverity.HIGH for signal in self.signals)

    def category_counts(self) -> dict[str, int]:
        """Return only safe category counts for persistence."""

        return {signal.category.value: signal.count for signal in self.signals}


_RULES: tuple[tuple[SafetyCategory, SafetySeverity, re.Pattern[str]], ...] = (
    (
        SafetyCategory.SECRET_MATERIAL,
        SafetySeverity.HIGH,
        re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |PGP )?PRIVATE KEY-----", re.I),
    ),
    (
        SafetyCategory.SECRET_MATERIAL,
        SafetySeverity.HIGH,
        re.compile(r"\b(?:sk-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16})\b"),
    ),
    (
        SafetyCategory.BIDI_CONTROL,
        SafetySeverity.LOW,
        re.compile("[\u202a-\u202e\u2066-\u2069]"),
    ),
    (
        SafetyCategory.ZERO_WIDTH_CONTROL,
        SafetySeverity.LOW,
        re.compile("[\u200b-\u200f\u2060\ufeff]"),
    ),
    (
        SafetyCategory.LONG_ENCODING,
        SafetySeverity.LOW,
        re.compile(
            r"(?<![A-Za-z0-9+/=])(?:[A-Fa-f0-9]{128,}|[A-Za-z0-9+/]{128,}={0,2})(?![A-Za-z0-9+/=])"
        ),
    ),
    (
        SafetyCategory.INSTRUCTION_INJECTION,
        SafetySeverity.HIGH,
        re.compile(
            r"\b(?:ignore (?:all |any |the |previous )?(?:system )?(?:rules|instructions)|"
            r"reveal (?:the )?system prompt|"
            r"(?:execute|invoke|call) (?:a |the )?(?:tool|command))\b",
            re.I,
        ),
    ),
)


class DocumentSafetyScanner:
    """Scan transient text and retain only aggregated categories."""

    def scan(self, text: str) -> SafetyReport:
        counts: Counter[tuple[SafetyCategory, SafetySeverity]] = Counter()
        for category, severity, pattern in _RULES:
            matches = sum(1 for _ in pattern.finditer(text))
            if matches:
                counts[(category, severity)] += matches
        signals = tuple(
            SafetySignal(category=category, severity=severity, count=count)
            for (category, severity), count in sorted(
                counts.items(), key=lambda item: (item[0][0].value, item[0][1].value)
            )
        )
        return SafetyReport(signals=signals)


__all__ = [
    "DocumentSafetyScanner",
    "SafetyCategory",
    "SafetyReport",
    "SafetySeverity",
    "SafetySignal",
]

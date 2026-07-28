"""Safe failures and stable refusal reasons for trusted answers."""

from __future__ import annotations


class AnswerError(RuntimeError):
    """Base failure safe to map at the API boundary."""


class AnswerIntegrityError(AnswerError):
    """Raised when model output or citation lineage violates a closed-set contract."""


class AnswerUnavailableError(AnswerError):
    """Raised when a required trusted dependency is unavailable."""


__all__ = ["AnswerError", "AnswerIntegrityError", "AnswerUnavailableError"]

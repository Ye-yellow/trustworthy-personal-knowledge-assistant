"""Safe errors shared by domain and persistence boundaries."""

from __future__ import annotations


class DomainError(RuntimeError):
    """Base error for deterministic domain-rule failures."""


class InvalidStateTransitionError(DomainError):
    """A requested state change is not declared by its state machine."""


class InvariantViolationError(DomainError):
    """A cross-record domain invariant would be violated."""


__all__ = ["DomainError", "InvalidStateTransitionError", "InvariantViolationError"]

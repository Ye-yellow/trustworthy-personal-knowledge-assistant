"""Safe, provider-neutral errors for governance workflows."""


class GovernanceError(RuntimeError):
    """Base error safe to classify without leaking content or credentials."""


class SearchCapabilityUnavailableError(GovernanceError):
    """The configured provider cannot satisfy the search contract."""


class SearchProviderError(GovernanceError):
    """The search provider failed transiently or returned an invalid response."""


class UnsafeFetchTargetError(GovernanceError):
    """A candidate URL failed the deterministic network policy."""


class EvidenceFetchError(GovernanceError):
    """A permitted target could not be fetched within the configured limits."""


class EvidencePackIntegrityError(GovernanceError):
    """A content-addressed evidence artifact failed hash or schema validation."""


class GovernanceConflictError(GovernanceError):
    """An idempotency or optimistic-concurrency invariant was violated."""


__all__ = [
    "EvidenceFetchError",
    "EvidencePackIntegrityError",
    "GovernanceConflictError",
    "GovernanceError",
    "SearchCapabilityUnavailableError",
    "SearchProviderError",
    "UnsafeFetchTargetError",
]

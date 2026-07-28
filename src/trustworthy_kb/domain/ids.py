"""Strongly typed, prefixed ULID identifiers."""

from __future__ import annotations

from typing import ClassVar

from pydantic_core import core_schema
from ulid import ULID


class TypedId(str):
    """Base class for canonical ``prefix_ULID`` identifiers."""

    prefix: ClassVar[str]

    def __new__(cls, value: str) -> TypedId:
        text = str(value).strip()
        if not text.startswith(cls.prefix):
            raise ValueError(f"expected prefix '{cls.prefix}'")
        raw_ulid = text.removeprefix(cls.prefix)
        try:
            canonical_ulid = str(ULID.from_str(raw_ulid))
        except (TypeError, ValueError) as error:
            raise ValueError("invalid ULID") from error
        return str.__new__(cls, f"{cls.prefix}{canonical_ulid}")

    @classmethod
    def generate(cls) -> TypedId:
        """Generate a new typed identifier."""

        return cls(f"{cls.prefix}{ULID()}")

    @classmethod
    def __get_pydantic_core_schema__(
        cls,
        _source_type: object,
        _handler: object,
    ) -> core_schema.CoreSchema:
        validator = parse_typed_id if cls is TypedId else cls
        return core_schema.no_info_after_validator_function(validator, core_schema.str_schema())


class SourceId(TypedId):
    prefix = "source_"


class SourceVersionId(TypedId):
    prefix = "srcver_"


class ContentBlockId(TypedId):
    prefix = "block_"


class ClaimId(TypedId):
    prefix = "claim_"


class EvidenceFamilyId(TypedId):
    prefix = "evfam_"


class EvidenceId(TypedId):
    prefix = "evidence_"


class QualityCheckId(TypedId):
    prefix = "qcheck_"


class KnowledgeChangeId(TypedId):
    prefix = "change_"


class KnowledgeNoteId(TypedId):
    prefix = "note_"


class CuratedVersionId(TypedId):
    prefix = "curated_"


class LineageEdgeId(TypedId):
    prefix = "lineage_"


class IndexGenerationId(TypedId):
    prefix = "idxgen_"


class IndexJobId(TypedId):
    prefix = "idxjob_"


class ModelRunId(TypedId):
    prefix = "modelrun_"


class OperationLogId(TypedId):
    prefix = "oplog_"


class IdempotencyRecordId(TypedId):
    prefix = "idem_"


class IngestionRunId(TypedId):
    prefix = "ingrun_"


class IngestionItemId(TypedId):
    prefix = "ingitem_"


ALL_ID_TYPES: tuple[type[TypedId], ...] = (
    SourceId,
    SourceVersionId,
    ContentBlockId,
    ClaimId,
    EvidenceFamilyId,
    EvidenceId,
    QualityCheckId,
    KnowledgeChangeId,
    KnowledgeNoteId,
    CuratedVersionId,
    LineageEdgeId,
    IndexGenerationId,
    IndexJobId,
    ModelRunId,
    OperationLogId,
    IdempotencyRecordId,
    IngestionRunId,
    IngestionItemId,
)


def parse_typed_id(value: str) -> TypedId:
    """Parse a value into the matching concrete ID type."""

    for id_type in ALL_ID_TYPES:
        if value.startswith(id_type.prefix):
            return id_type(value)
    raise ValueError("unknown ID prefix")


__all__ = [
    "ALL_ID_TYPES",
    "ClaimId",
    "ContentBlockId",
    "CuratedVersionId",
    "EvidenceFamilyId",
    "EvidenceId",
    "IdempotencyRecordId",
    "IndexGenerationId",
    "IndexJobId",
    "IngestionItemId",
    "IngestionRunId",
    "KnowledgeChangeId",
    "KnowledgeNoteId",
    "LineageEdgeId",
    "ModelRunId",
    "OperationLogId",
    "QualityCheckId",
    "SourceId",
    "SourceVersionId",
    "TypedId",
    "parse_typed_id",
]

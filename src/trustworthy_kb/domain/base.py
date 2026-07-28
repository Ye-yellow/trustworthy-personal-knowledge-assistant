"""Shared validation primitives for immutable domain records."""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints

type DomainJson = dict[str, Any]
NonEmptyText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Sha256Hex = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
Revision = Annotated[int, Field(ge=1)]
NonNegativeInt = Annotated[int, Field(ge=0)]
UnitScore = Annotated[float, Field(ge=0, le=1)]


class DomainRecord(BaseModel):
    """Immutable, strict base model for data returned by repositories."""

    model_config = ConfigDict(frozen=True, extra="forbid")


__all__ = [
    "AwareDatetime",
    "DomainJson",
    "DomainRecord",
    "NonEmptyText",
    "NonNegativeInt",
    "Revision",
    "Sha256Hex",
    "UnitScore",
]

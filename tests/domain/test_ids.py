from __future__ import annotations

import pytest
from pydantic import BaseModel, ValidationError

from trustworthy_kb.domain import ALL_ID_TYPES, ClaimId, SourceId, parse_typed_id


class ClaimReference(BaseModel):
    claim_id: ClaimId


def test_all_typed_ids_generate_canonical_prefixed_ulids() -> None:
    generated = [id_type.generate() for id_type in ALL_ID_TYPES]

    assert len(set(generated)) == len(generated)
    for value, id_type in zip(generated, ALL_ID_TYPES, strict=True):
        assert isinstance(value, id_type)
        assert value.startswith(id_type.prefix)
        assert len(value.removeprefix(id_type.prefix)) == 26
        assert value == value.upper().replace(id_type.prefix.upper(), id_type.prefix, 1)


def test_typed_id_rejects_wrong_prefix_and_invalid_ulid() -> None:
    source_id = SourceId.generate()

    with pytest.raises(ValueError, match="expected prefix"):
        ClaimId(source_id)
    with pytest.raises(ValueError, match="invalid ULID"):
        SourceId("source_not-a-ulid")


def test_typed_id_integrates_with_pydantic_and_generic_parser() -> None:
    claim_id = ClaimId.generate()

    model = ClaimReference(claim_id=str(claim_id))

    assert model.claim_id == claim_id
    assert isinstance(model.claim_id, ClaimId)
    assert parse_typed_id(str(claim_id)) == claim_id
    with pytest.raises(ValidationError):
        ClaimReference(claim_id=str(SourceId.generate()))
    with pytest.raises(ValueError, match="unknown ID prefix"):
        parse_typed_id("unknown_01ARZ3NDEKTSV4RRFFQ69G5FAV")

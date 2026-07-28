"""Stable project types for model selection and results."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from langchain_core.language_models.chat_models import BaseChatModel
from pydantic import BaseModel, ConfigDict


class ModelPurpose(StrEnum):
    """Business purpose used to select a model configuration."""

    CLAIM_EXTRACTION = "claim_extraction"
    EVIDENCE_VERIFICATION = "evidence_verification"
    CURATION = "curation"
    ANSWER_GENERATION = "answer_generation"
    EVIDENCE_SEARCH = "evidence_search"


@dataclass(frozen=True, slots=True)
class RoutedModel:
    """A LangChain model plus non-secret routing metadata."""

    purpose: ModelPurpose
    provider: str
    model_name: str
    chat_model: BaseChatModel


class ModelResult(BaseModel):
    """Provider-neutral result returned to business code."""

    model_config = ConfigDict(frozen=True)

    text: str
    provider: str
    model: str
    purpose: ModelPurpose
    finish_reason: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    response_id: str | None = None

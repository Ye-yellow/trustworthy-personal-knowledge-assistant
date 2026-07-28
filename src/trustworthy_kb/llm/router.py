"""Purpose-aware model routing."""

from __future__ import annotations

from collections.abc import Callable

from langchain_core.language_models.chat_models import BaseChatModel

from trustworthy_kb.config import LLMSettings
from trustworthy_kb.llm.factory import create_chat_model
from trustworthy_kb.llm.types import ModelPurpose, RoutedModel

ModelFactory = Callable[[LLMSettings, str], BaseChatModel]


class ModelRouter:
    """Resolve and cache LangChain models by business purpose."""

    def __init__(self, settings: LLMSettings, *, factory: ModelFactory = create_chat_model) -> None:
        self._settings = settings
        self._factory = factory
        self._models: dict[str, BaseChatModel] = {}

    def route(self, purpose: ModelPurpose) -> RoutedModel:
        """Return the configured model and safe routing metadata for ``purpose``."""

        model_name = self._model_name(purpose)
        chat_model = self._models.get(model_name)
        if chat_model is None:
            chat_model = self._factory(self._settings, model_name)
            self._models[model_name] = chat_model
        return RoutedModel(
            purpose=purpose,
            provider=self._settings.provider,
            model_name=model_name,
            chat_model=chat_model,
        )

    def _model_name(self, purpose: ModelPurpose) -> str:
        override = {
            ModelPurpose.CLAIM_EXTRACTION: self._settings.extractor_model,
            ModelPurpose.EVIDENCE_VERIFICATION: self._settings.verifier_model,
            ModelPurpose.CURATION: self._settings.curation_model,
            ModelPurpose.ANSWER_GENERATION: self._settings.answer_model,
            ModelPurpose.EVIDENCE_SEARCH: self._settings.model,
        }[purpose]
        return override or self._settings.model

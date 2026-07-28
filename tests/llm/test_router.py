from __future__ import annotations

from typing import Any

from trustworthy_kb.config import LLMSettings
from trustworthy_kb.llm import ModelPurpose, ModelRouter


def test_router_selects_purpose_override_and_caches_by_model() -> None:
    created: list[str] = []

    def factory(_settings: LLMSettings, model_name: str) -> Any:
        created.append(model_name)
        return object()

    settings = LLMSettings(
        api_key="router-secret",
        extractor_model="fast-model",
        verifier_model="strong-model",
    )
    router = ModelRouter(settings, factory=factory)

    first = router.route(ModelPurpose.CLAIM_EXTRACTION)
    second = router.route(ModelPurpose.CLAIM_EXTRACTION)
    verifier = router.route(ModelPurpose.EVIDENCE_VERIFICATION)
    answer = router.route(ModelPurpose.ANSWER_GENERATION)

    assert first.chat_model is second.chat_model
    assert first.model_name == "fast-model"
    assert verifier.model_name == "strong-model"
    assert answer.model_name == "gpt-5.5"
    assert first.provider == "sub2api"
    assert created == ["fast-model", "strong-model", "gpt-5.5"]


def test_router_uses_default_model_for_curation() -> None:
    settings = LLMSettings(api_key="router-secret")
    router = ModelRouter(settings, factory=lambda _settings, _model_name: object())

    selection = router.route(ModelPurpose.CURATION)

    assert selection.model_name == "gpt-5.5"

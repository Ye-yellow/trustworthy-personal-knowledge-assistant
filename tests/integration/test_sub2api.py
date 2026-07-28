from __future__ import annotations

import os

import httpx
import pytest

from trustworthy_kb.config import LLMSettings
from trustworthy_kb.llm import ModelGateway, ModelPurpose, ModelRouter


@pytest.mark.integration
@pytest.mark.asyncio
async def test_local_sub2api_models_and_chat_completion() -> None:
    if os.environ.get("TRUSTKB_RUN_SUB2API_INTEGRATION") != "1":
        pytest.skip("set TRUSTKB_RUN_SUB2API_INTEGRATION=1 to call local sub2api")

    settings = LLMSettings(_env_file=".env")
    if settings.provider != "sub2api":
        pytest.fail("sub2api integration test requires TRUSTKB_LLM_PROVIDER=sub2api")
    assert settings.base_url is not None
    assert settings.api_key is not None

    headers = {"Authorization": f"Bearer {settings.api_key.get_secret_value()}"}
    async with httpx.AsyncClient(timeout=settings.timeout_seconds) as client:
        response = await client.get(f"{settings.base_url}/models", headers=headers)
        response.raise_for_status()
    model_ids = {item["id"] for item in response.json().get("data", [])}
    assert settings.model in model_ids

    gateway = ModelGateway(ModelRouter(settings))
    result = await gateway.invoke(
        "Reply with exactly SUB2API_OK and no other text.",
        purpose=ModelPurpose.ANSWER_GENERATION,
        tags=["integration", "synthetic"],
    )

    assert result.text.strip() == "SUB2API_OK"
    assert result.provider == "sub2api"
    assert result.model

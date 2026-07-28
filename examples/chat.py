"""Minimal provider-neutral chat example."""

from __future__ import annotations

import asyncio

from trustworthy_kb.config import LLMSettings
from trustworthy_kb.llm import ModelGateway, ModelPurpose, ModelRouter


async def main() -> None:
    """Send a synthetic prompt through the configured provider."""

    settings = LLMSettings()
    gateway = ModelGateway(ModelRouter(settings))
    result = await gateway.invoke(
        "Reply with exactly: gateway ready",
        purpose=ModelPurpose.ANSWER_GENERATION,
        tags=["example"],
    )
    print(result.text)


if __name__ == "__main__":
    asyncio.run(main())

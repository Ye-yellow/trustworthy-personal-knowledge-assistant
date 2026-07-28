"""Application configuration."""

from trustworthy_kb.config.database import DatabaseSettings
from trustworthy_kb.config.settings import LLMSettings

__all__ = ["DatabaseSettings", "LLMSettings"]

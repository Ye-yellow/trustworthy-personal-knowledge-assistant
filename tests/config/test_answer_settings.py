import pytest
from pydantic import ValidationError

from trustworthy_kb.config import AnswerSettings


def test_answer_settings_default_to_loopback_and_bounded_policy() -> None:
    settings = AnswerSettings()

    assert settings.api_host == "127.0.0.1"
    assert settings.api_port == 8765
    assert settings.max_answer_claims == 12
    assert settings.min_evidence_count <= settings.default_top_k


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.10", "localhost", "example.test"])
def test_answer_settings_reject_non_ip_or_non_loopback_hosts(host: str) -> None:
    with pytest.raises(ValidationError, match="loopback"):
        AnswerSettings(api_host=host)


def test_answer_settings_reject_impossible_evidence_budget() -> None:
    with pytest.raises(ValidationError, match="top-k"):
        AnswerSettings(min_evidence_count=6, default_top_k=5)

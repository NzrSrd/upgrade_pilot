"""One real call, asserting the usage surface Phase 4's TrackedLLM will read.

Every other token-tracking test uses a fake model with synthetic usage
metadata, so all of them can pass while the real extractor is broken and
the counter reads zero. This test is the only thing that closes that gap.
"""

import pytest
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

from upgradepilot.config import get_settings

pytestmark = pytest.mark.live


class Verdict(BaseModel):
    sufficient: bool = Field(description="whether the evidence suffices")
    reason: str


@pytest.fixture
def model() -> ChatOpenAI:
    settings = get_settings()
    if not settings.llm_configured:
        pytest.skip("no LLM API key configured")
    # Rule 18 exception, deliberate and scoped: TrackedLLM is the only place
    # application code may construct a chat model. This test exists to
    # establish what TrackedLLM's extractor must read, so it has to bypass it.
    # `llm_api_key` is a SecretStr, so the value has to be unwrapped
    # explicitly. `llm_configured` above guarantees it is not None.
    #
    # `base_url=None` is the OpenAI default, so this test runs unchanged
    # against either provider -- which is the point: what it asserts is that
    # `usage_metadata` is populated by whatever endpoint is configured, and
    # a pass against one provider is not a claim about the other.
    assert settings.llm_api_key is not None
    return ChatOpenAI(
        model=settings.chat_model,
        api_key=settings.llm_api_key.get_secret_value(),
        base_url=settings.llm_base_url,
        temperature=0,
    )


def test_plain_invoke_populates_usage_metadata(model: ChatOpenAI) -> None:
    message = model.invoke("Reply with the single word: ok")

    assert message.usage_metadata is not None, "usage_metadata absent; fallback path required"
    assert message.usage_metadata["input_tokens"] > 0
    assert message.usage_metadata["output_tokens"] > 0
    assert message.usage_metadata["total_tokens"] == (
        message.usage_metadata["input_tokens"] + message.usage_metadata["output_tokens"]
    )


def test_structured_output_with_include_raw_preserves_usage(model: ChatOpenAI) -> None:
    """The hazard in spec §9.4: structured output can swallow the raw message."""
    structured = model.with_structured_output(Verdict, include_raw=True)
    result = structured.invoke("Is one sentence enough evidence for a migration? Answer briefly.")

    assert set(result) >= {"raw", "parsed"}
    assert isinstance(result["parsed"], Verdict)

    usage = result["raw"].usage_metadata
    assert usage is not None, "include_raw did not preserve usage metadata"
    assert usage["input_tokens"] > 0 and usage["output_tokens"] > 0

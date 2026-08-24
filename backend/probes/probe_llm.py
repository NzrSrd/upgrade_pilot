"""Phase 0 probe: which usage surface the pinned langchain-core populates.

Run from the backend/ directory: .venv/bin/python probes/probe_llm.py
Requires a real OPENROUTER_API_KEY (or OPENAI_API_KEY) in backend/.env.
Costs a fraction of a cent.

Rule 18 exception: this constructs ChatOpenAI directly. That rule binds
application code, where TrackedLLM is the only permitted construction site so
token usage cannot be missed. This is Phase 0 verification code whose entire
purpose is to observe the raw usage surface *before* TrackedLLM exists to wrap
it. Nothing under src/ may copy this pattern.
"""

from langchain_openai import ChatOpenAI
from pydantic import BaseModel

from upgradepilot.config import get_settings


class Verdict(BaseModel):
    sufficient: bool
    reason: str


def main() -> None:
    settings = get_settings()
    if not settings.llm_configured:
        raise SystemExit("no API key — set OPENROUTER_API_KEY in backend/.env first")

    # `.get_secret_value()`, not the SecretStr itself. Passing the wrapper
    # sent the literal string "**********" as the bearer token, so the probe
    # failed with a 401 that read like a bad key rather than like a bug here.
    assert settings.llm_api_key is not None
    model = ChatOpenAI(
        model=settings.chat_model,
        api_key=settings.llm_api_key.get_secret_value(),
        base_url=settings.llm_base_url,
        temperature=0,
    )

    message = model.invoke("Reply with the single word: ok")
    print(f"base_url                  : {settings.llm_base_url or '<provider default>'}")
    print(f"model                     : {settings.chat_model}")
    print(f"usage_metadata            : {message.usage_metadata}")
    usage = message.response_metadata.get("token_usage") or {}
    print(f"response_metadata usage   : {usage}")
    # OpenRouter reports the real charge per call; OpenAI direct does not.
    print(f"cost reported by provider : {usage.get('cost')}")

    structured = model.with_structured_output(Verdict, include_raw=True)
    result = structured.invoke("Is one sentence enough evidence? Answer briefly.")
    print(f"structured keys           : {sorted(result)}")
    print(f"parsed type               : {type(result['parsed']).__name__}")
    print(f"usage survives include_raw: {result['raw'].usage_metadata}")


if __name__ == "__main__":
    main()

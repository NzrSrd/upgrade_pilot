"""Phase 0 probe: which usage surface the pinned langchain-core populates.

Run: backend/.venv/bin/python probes/probe_llm.py
Requires a real OPENAI_API_KEY in backend/.env. Costs a fraction of a cent.
"""

from langchain_openai import ChatOpenAI
from pydantic import BaseModel

from upgradepilot.config import get_settings


class Verdict(BaseModel):
    sufficient: bool
    reason: str


def main() -> None:
    settings = get_settings()
    if not settings.openai_configured:
        raise SystemExit("OPENAI_API_KEY missing — set it in backend/.env first")

    model = ChatOpenAI(model=settings.chat_model, api_key=settings.openai_api_key, temperature=0)

    message = model.invoke("Reply with the single word: ok")
    print(f"model                     : {settings.chat_model}")
    print(f"usage_metadata            : {message.usage_metadata}")
    print(f"response_metadata usage   : {message.response_metadata.get('token_usage')}")

    structured = model.with_structured_output(Verdict, include_raw=True)
    result = structured.invoke("Is one sentence enough evidence? Answer briefly.")
    print(f"structured keys           : {sorted(result)}")
    print(f"parsed type               : {type(result['parsed']).__name__}")
    print(f"usage survives include_raw: {result['raw'].usage_metadata}")


if __name__ == "__main__":
    main()

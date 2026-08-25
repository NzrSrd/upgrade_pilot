"""A scripted chat model: deterministic responses with synthetic usage.

Spec §11 layer 3. Every graph-path test drives this instead of a provider, so
the paths under test -- RAG refinement, the sufficiency gate overriding a
falsely-confident model, interrupt and resume, the decision flip -- run the
same way every time.

**What this fake is allowed to assume, and why that is safe.** It reproduces
the `with_structured_output(schema, include_raw=True)` contract *as Phase 0
measured it against the pinned stack*: a mapping with `parsed`,
`parsing_error` and `raw`, where `raw` is an `AIMessage` carrying
`usage_metadata`. That measurement is recorded in ADR-001 and re-checked on
every `--live` run by `tests/llm/test_usage_metadata_live.py`. So the fake is
pinned to a verified contract rather than to a guess -- which matters, because
a fake that invented a friendlier shape would let every token-tracking test
pass while the real extractor read the wrong field.

The scripted responses can also *withhold* usage, which is the point of
`ScriptedResponse.usage`: `TrackedLLM` has three extraction paths and two of
them only ever run when a provider reports less than the happy path does.
"""

from collections.abc import Sequence
from typing import Any, Literal

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import Runnable, RunnableLambda
from pydantic import BaseModel, Field

UsageSurface = Literal["usage_metadata", "token_usage", "none"]
"""Which surface a scripted response populates.

- `usage_metadata` -- LangChain's native field, the primary extraction path.
- `token_usage` -- OpenAI's `response_metadata["token_usage"]` shape, the
  fallback. Phase 0 found both populated on the pinned stack; the fallback
  exists because which one is populated is version-dependent.
- `none` -- neither. Forces the tiktoken estimation path, which is the only
  one that may report `tokens_estimated=True`.
"""


class ScriptedResponse(BaseModel):
    """One queued answer."""

    model_config = {"arbitrary_types_allowed": True}

    parsed: BaseModel | None = None
    """What `with_structured_output` should return as `parsed`."""

    text: str = "scripted response"
    """The raw message content. Also what the estimator counts when `usage`
    is `none`, so it is not merely decorative."""

    input_tokens: int = 100
    output_tokens: int = 20
    usage: UsageSurface = "usage_metadata"
    provider_cost: float | None = None
    """Populates `token_usage["cost"]`, the field OpenRouter returns and
    OpenAI direct does not."""

    parsing_error: str | None = None
    """When set, the response carries a parse failure the way the real
    `include_raw=True` path does -- returned in the mapping, not raised."""


def _raw_message(response: ScriptedResponse) -> AIMessage:
    """Build the `AIMessage` the real provider path would produce."""
    message = AIMessage(content=response.text)

    if response.usage == "usage_metadata":
        message.usage_metadata = {
            "input_tokens": response.input_tokens,
            "output_tokens": response.output_tokens,
            "total_tokens": response.input_tokens + response.output_tokens,
        }

    token_usage: dict[str, Any] = {}
    if response.usage == "token_usage":
        token_usage = {
            "prompt_tokens": response.input_tokens,
            "completion_tokens": response.output_tokens,
            "total_tokens": response.input_tokens + response.output_tokens,
        }
    if response.provider_cost is not None:
        token_usage["cost"] = response.provider_cost
    if token_usage:
        message.response_metadata = {"token_usage": token_usage}

    return message


class ScriptedChatModel(BaseChatModel):
    """Returns queued responses in order, and records what it was asked."""

    responses: list[ScriptedResponse] = Field(default_factory=list)
    prompts: list[str] = Field(default_factory=list)
    """Every prompt this model was handed, in order. Lets a test assert that
    a refinement round actually issued a *different* query rather than
    repeating the first one -- which is otherwise invisible."""

    raise_on_call: Exception | None = None
    """When set, every call raises it. Used to exercise the error taxonomy."""

    model_config = {"arbitrary_types_allowed": True}

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def _next(self, messages: Sequence[BaseMessage]) -> ScriptedResponse:
        self.prompts.append("\n".join(str(message.content) for message in messages))
        if self.raise_on_call is not None:
            raise self.raise_on_call
        if not self.responses:
            raise AssertionError(
                "the scripted model ran out of responses: the code under test made more "
                "calls than the test scripted, which is itself worth knowing"
            )
        return self.responses.pop(0)

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        response = self._next(messages)
        return ChatResult(generations=[ChatGeneration(message=_raw_message(response))])

    def with_structured_output(
        self,
        schema: Any = None,
        *,
        include_raw: bool = False,
        **kwargs: Any,
    ) -> Runnable[Any, Any]:
        """Reproduce the measured `include_raw=True` contract.

        Overridden rather than inherited because the inherited implementation
        routes through tool calling, which a fake would have to simulate in
        far more detail to end up at the same three keys. Producing the
        mapping directly keeps the fake small and pinned to the shape Phase 0
        actually observed.

        `include_raw=False` is refused rather than approximated: `TrackedLLM`
        never uses it, because dropping the raw message drops the usage
        metadata with it -- the hazard spec §9.4 names -- and a fake that
        quietly supported the unsafe call would make it look available.
        """
        if not include_raw:
            raise NotImplementedError(
                "TrackedLLM always passes include_raw=True: without the raw message the "
                "usage metadata is lost (spec §9.4 hazard 2), so this fake does not "
                "model the unsafe call"
            )

        def _invoke(prompt: Any) -> dict[str, Any]:
            messages = prompt if isinstance(prompt, list) else [prompt]
            response = self._next(
                [m if isinstance(m, BaseMessage) else AIMessage(content=str(m)) for m in messages]
            )
            return {
                "raw": _raw_message(response),
                "parsed": response.parsed,
                "parsing_error": (
                    ValueError(response.parsing_error) if response.parsing_error else None
                ),
            }

        return RunnableLambda(_invoke)

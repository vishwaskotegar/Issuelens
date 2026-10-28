"""Unit tests for LangChain LLM provider adapters, structured output, and error translation."""

import pytest
from langchain_anthropic import ChatAnthropic
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import RunnableLambda
from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from issuelens.agent.llm import (
    LangChainLLMClient,
    MockLLMClient,
    create_langchain_chat_model,
    get_llm_client,
)
from issuelens.agent.report import Hypothesis, InvestigationReportPayload
from issuelens.config import Settings
from issuelens.core.errors import (
    IssueLensError,
    LLMOutputValidationError,
    RateLimitExceededError,
    SafetyViolationError,
    UpstreamTimeoutError,
)


class FakeStructuredChatModel(BaseChatModel):
    """Fake BaseChatModel returning predetermined structured output for tests."""

    response_payload: InvestigationReportPayload | dict[str, object] | None = None
    raise_on_invoke: Exception | None = None

    @property
    def _llm_type(self) -> str:
        return "fake-structured-model"

    def _generate(  # type: ignore[override]
        self,
        _messages: list[BaseMessage],
        _stop: list[str] | None = None,
        _run_manager: object = None,
        **_kwargs: object,
    ) -> ChatResult:
        return ChatResult(generations=[ChatGeneration(message=HumanMessage(content="ok"))])

    def with_structured_output(  # type: ignore[override]
        self,
        _schema: object,
        *,
        _include_raw: bool = False,
        **_kwargs: object,
    ) -> object:
        def _invoke(_messages: list[BaseMessage]) -> object:
            if self.raise_on_invoke:
                raise self.raise_on_invoke
            if self.response_payload is not None:
                return self.response_payload
            return InvestigationReportPayload(
                summary="Fake summary",
                observed_facts=["Fact 1"],
                hypotheses=[
                    Hypothesis(
                        title="Fake title",
                        explanation="Fake explanation",
                        confidence="high",
                        supporting_evidence_ids=["ev_1"],
                    )
                ],
            )

        return RunnableLambda(_invoke)


@pytest.mark.asyncio
async def test_langchain_client_success() -> None:
    """Verify LangChainLLMClient invokes structured model and returns typed payload."""
    fake_model = FakeStructuredChatModel()
    client = LangChainLLMClient(chat_model=fake_model)

    messages: list[BaseMessage] = [
        SystemMessage(content="system"),
        HumanMessage(content="user"),
    ]
    payload = await client.generate_report_payload(messages)

    assert isinstance(payload, InvestigationReportPayload)
    assert payload.summary == "Fake summary"
    assert len(payload.hypotheses) == 1
    assert payload.hypotheses[0].title == "Fake title"


@pytest.mark.asyncio
async def test_langchain_client_dict_response_validated() -> None:
    """Verify dict responses returned by structured output are validated into payload."""
    fake_model = FakeStructuredChatModel(
        response_payload={
            "summary": "Dict summary",
            "observed_facts": ["Fact from dict"],
            "hypotheses": [],
        }
    )
    client = LangChainLLMClient(chat_model=fake_model)

    payload = await client.generate_report_payload([HumanMessage(content="hi")])
    assert payload.summary == "Dict summary"
    assert payload.observed_facts == ["Fact from dict"]


@pytest.mark.asyncio
async def test_langchain_client_invalid_schema_error() -> None:
    """Verify invalid payloads raise LLMOutputValidationError."""
    fake_model = FakeStructuredChatModel(
        response_payload={"hypotheses": "not a list"}  # Invalid type
    )
    client = LangChainLLMClient(chat_model=fake_model)

    with pytest.raises(LLMOutputValidationError):
        await client.generate_report_payload([HumanMessage(content="hi")])


@pytest.mark.asyncio
async def test_langchain_client_rate_limit_translation() -> None:
    """Verify upstream rate limits translate to RateLimitExceededError."""
    fake_model = FakeStructuredChatModel(
        raise_on_invoke=Exception("Rate limit 429 reached")
    )
    client = LangChainLLMClient(chat_model=fake_model)

    with pytest.raises(RateLimitExceededError):
        await client.generate_report_payload([HumanMessage(content="hi")])


@pytest.mark.asyncio
async def test_langchain_client_timeout_translation() -> None:
    """Verify upstream timeouts translate to UpstreamTimeoutError."""
    fake_model = FakeStructuredChatModel(
        raise_on_invoke=TimeoutError("Request timed out")
    )
    client = LangChainLLMClient(chat_model=fake_model)

    with pytest.raises(UpstreamTimeoutError):
        await client.generate_report_payload([HumanMessage(content="hi")])


@pytest.mark.asyncio
async def test_langchain_client_auth_error_translation() -> None:
    """Verify upstream authentication errors translate to SafetyViolationError."""
    fake_model = FakeStructuredChatModel(
        raise_on_invoke=Exception("Invalid API key provided: 401 Unauthorized")
    )
    client = LangChainLLMClient(chat_model=fake_model)

    with pytest.raises(SafetyViolationError, match="Check LLM_API_KEY"):
        await client.generate_report_payload([HumanMessage(content="hi")])


@pytest.mark.asyncio
async def test_langchain_client_generic_error_translation() -> None:
    """Verify unknown errors translate to IssueLensError."""
    fake_model = FakeStructuredChatModel(
        raise_on_invoke=RuntimeError("Unexpected connection reset")
    )
    client = LangChainLLMClient(chat_model=fake_model)

    with pytest.raises(IssueLensError, match="LangChain model invocation failed"):
        await client.generate_report_payload([HumanMessage(content="hi")])


@pytest.mark.asyncio
async def test_mock_llm_client_default_and_custom() -> None:
    """Verify MockLLMClient records calls and returns expected payload."""
    client = MockLLMClient()
    payload = await client.generate_report_payload([HumanMessage(content="test")])

    assert payload.summary.startswith("Mock investigation")
    assert len(client.calls) == 1
    assert len(payload.hypotheses) == 1

    # Override payload
    custom = InvestigationReportPayload(summary="Custom override")
    custom_client = MockLLMClient(payload_override=custom)
    res = await custom_client.generate_report_payload([])
    assert res.summary == "Custom override"

    # Simulated failure
    failing = MockLLMClient(fail_with=IssueLensError("Simulated LLM error"))
    with pytest.raises(IssueLensError, match="Simulated LLM error"):
        await failing.generate_report_payload([])


def test_create_langchain_chat_model_openai() -> None:
    """Verify OpenAI chat model creation."""
    settings = Settings(
        llm_provider="openai",
        llm_model="gpt-4o",
        llm_api_key=SecretStr("sk-openai-key"),
    )
    model = create_langchain_chat_model(settings)
    assert isinstance(model, ChatOpenAI)
    assert model.model_name == "gpt-4o"


def test_create_langchain_chat_model_anthropic() -> None:
    """Verify Anthropic chat model creation."""
    settings = Settings(
        llm_provider="anthropic",
        llm_model="claude-3-5-sonnet-20241022",
        llm_api_key=SecretStr("sk-ant-key"),
    )
    model = create_langchain_chat_model(settings)
    assert isinstance(model, ChatAnthropic)
    assert model.model == "claude-3-5-sonnet-20241022"


def test_get_llm_client_factory() -> None:
    """Verify get_llm_client returns appropriate protocol implementations."""
    mock_settings = Settings(llm_provider="mock")
    assert isinstance(get_llm_client(mock_settings), MockLLMClient)

    openai_settings = Settings(llm_provider="openai", llm_api_key=SecretStr("key"))
    client = get_llm_client(openai_settings)
    assert isinstance(client, LangChainLLMClient)
    assert isinstance(client.chat_model, ChatOpenAI)

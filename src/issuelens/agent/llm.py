"""LangChain-based LLM provider adapters with structured output and error translation."""

from typing import Protocol

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage
from pydantic import SecretStr, ValidationError

from issuelens.agent.report import Hypothesis, InvestigationReportPayload
from issuelens.config import Settings
from issuelens.core.errors import (
    IssueLensError,
    LLMOutputValidationError,
    RateLimitExceededError,
    SafetyViolationError,
    UpstreamTimeoutError,
)
from issuelens.core.logging import get_logger

logger = get_logger("issuelens.agent.llm")


class LLMClientProtocol(Protocol):
    """Protocol defining LLM report payload generation for dependency injection."""

    async def generate_report_payload(
        self,
        messages: list[BaseMessage],
    ) -> InvestigationReportPayload:
        """Invoke LLM with structured output to produce an InvestigationReportPayload."""
        ...


class LangChainLLMClient:
    """LLM client adapter delegating completion to a LangChain BaseChatModel."""

    def __init__(self, chat_model: BaseChatModel) -> None:
        self.chat_model = chat_model
        # Configure schema-enforced structured output via LangChain
        self._structured_model = chat_model.with_structured_output(
            InvestigationReportPayload,
        )

    async def generate_report_payload(
        self,
        messages: list[BaseMessage],
    ) -> InvestigationReportPayload:
        """Invoke LangChain chat model and validate structured report payload."""
        try:
            result = await self._structured_model.ainvoke(messages)
            if isinstance(result, InvestigationReportPayload):
                return result
            if isinstance(result, dict):
                return InvestigationReportPayload.model_validate(result)
            raise LLMOutputValidationError(
                "LangChain structured output did not produce an InvestigationReportPayload."
            )
        except LLMOutputValidationError:
            raise
        except ValidationError as exc:
            raise LLMOutputValidationError(
                f"LangChain structured output failed Pydantic validation: {exc}"
            ) from exc
        except Exception as exc:
            err_str = str(exc).lower()
            if "rate limit" in err_str or "429" in err_str:
                raise RateLimitExceededError(
                    f"LLM provider rate limit exceeded: {exc}"
                ) from exc
            if "timeout" in err_str or "timed out" in err_str:
                raise UpstreamTimeoutError(
                    f"LLM request timed out: {exc}"
                ) from exc
            auth_markers = ("auth", "api_key", "api key", "401", "403", "unauthorized")
            if any(marker in err_str for marker in auth_markers):
                raise SafetyViolationError(
                    "LLM provider authentication failed. Check LLM_API_KEY."
                ) from exc

            raise IssueLensError(
                f"LangChain model invocation failed: {exc}",
                code="LLM_INVOCATION_ERROR",
                status_code=502,
            ) from exc


class MockLLMClient:
    """Deterministic offline mock LLM client conforming to LLMClientProtocol."""

    def __init__(
        self,
        payload_override: InvestigationReportPayload | None = None,
        fail_with: Exception | None = None,
    ) -> None:
        self.payload_override = payload_override
        self.fail_with = fail_with
        self.calls: list[list[BaseMessage]] = []

    async def generate_report_payload(
        self,
        messages: list[BaseMessage],
    ) -> InvestigationReportPayload:
        """Record invocation and return deterministic payload."""
        self.calls.append(messages)
        if self.fail_with:
            raise self.fail_with

        if self.payload_override is not None:
            return self.payload_override

        return InvestigationReportPayload(
            summary="Mock investigation identified probable cause in parameter parsing.",
            observed_facts=[
                "User reported unexpected ValueError on special character queries.",
                "Source file auth.py handles token verification.",
            ],
            hypotheses=[
                Hypothesis(
                    title="Unescaped query parameter encoding in Request.prepare_url",
                    explanation=(
                        "Query parameters are concatenated without encoding reserved chars."
                    ),
                    confidence="high",
                    confidence_rationale=(
                        "Directly matches the code excerpt in ev_1 and the reported traceback."
                    ),
                    supporting_evidence_ids=["ev_1"],
                    contradicting_evidence_ids=[],
                    verification_steps=[
                        "Inspect parameter formatting line in query preparation function.",
                        "Execute minimal repro script with encoded characters.",
                    ],
                )
            ],
            missing_information=[
                "Exact Python version in user environment was not specified."
            ],
            suggested_tests=[
                "test_query_encoding_with_special_characters",
            ],
            recommended_next_steps=[
                "Apply urllib.parse.quote_plus to query dictionary before formatting URL.",
            ],
            limitations=[
                "Investigation performed via static AST inspection without dynamic execution."
            ],
        )


def create_langchain_chat_model(settings: Settings) -> BaseChatModel:
    """Instantiate a configured LangChain BaseChatModel from application settings."""
    provider = (settings.llm_provider or "openai").lower()
    api_key_secret = settings.llm_api_key or SecretStr("")

    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(
            model_name=settings.llm_model or "claude-3-5-sonnet-20241022",
            api_key=api_key_secret,
            timeout=float(settings.request_timeout_seconds),
            max_retries=2,
            stop=None,
        )

    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        model=settings.llm_model or "gpt-4o",
        api_key=api_key_secret,
        timeout=float(settings.request_timeout_seconds),
        max_retries=2,
    )


def get_llm_client(
    settings: Settings,
    chat_model: BaseChatModel | None = None,
) -> LLMClientProtocol:
    """Factory creating an LLM client adapter matching application configuration."""
    if chat_model is not None:
        return LangChainLLMClient(chat_model)

    provider = (settings.llm_provider or "mock").lower()
    if provider == "mock":
        return MockLLMClient()

    model = create_langchain_chat_model(settings)
    return LangChainLLMClient(model)

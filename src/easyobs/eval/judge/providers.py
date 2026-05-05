"""Judge provider abstraction + a deterministic mock implementation.

The mock is the **default** so the system stays useful in air-gapped /
demo deployments and so tests never reach out to a paid API. It produces
stable scores derived from a hash of the request payload, which means
running the same trace through the same model twice always returns the
same answer — a property the consensus tests rely on.

Real providers can be registered through :func:`register_provider`. The
``OpenAI`` adapter included here is opt-in: it only imports the SDK if the
operator actually configures it.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

_log = logging.getLogger("easyobs.eval.judge")


@dataclass(frozen=True, slots=True)
class JudgeModelSpec:
    """Static description of one registered judge model.

    Pricing is captured per-model so the cost guard can project spend
    *before* the provider is ever called."""

    id: str
    provider: str
    model: str
    name: str
    weight: float = 1.0
    temperature: float = 0.0
    cost_per_1k_input: float = 0.0
    cost_per_1k_output: float = 0.0
    # Non-secret hints: api_key_env, base_url, region, deployment, etc.
    connection: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class JudgeRequest:
    rubric_id: str
    prompt: str
    context: dict[str, Any] = field(default_factory=dict)
    max_tokens: int = 1024
    system_prompt: str | None = None
    #: Full user message sent to the LLM. When ``None``, providers fall back to
    #: a JSON payload of rubric + context (legacy / tests).
    user_message: str | None = None


@dataclass(frozen=True, slots=True)
class JudgeResponse:
    score: float
    verdict: str
    reason: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    raw: dict[str, Any] = field(default_factory=dict)


class JudgeProviderError(Exception):
    """Raised by a provider when the call should be **excluded** from
    aggregation (12 §4). The runner catches this and stores the row with
    ``verdict='error'`` instead of silently degrading to a mock answer.

    ``error_type`` should be one of :class:`easyobs.eval.types.JudgeErrorType`
    (kept loose here to avoid an import cycle)."""

    __slots__ = ("error_type", "model_id", "retry_count", "detail")

    def __init__(
        self,
        error_type: str,
        *,
        model_id: str = "",
        retry_count: int = 0,
        detail: str = "",
    ) -> None:
        self.error_type = error_type
        self.model_id = model_id
        self.retry_count = retry_count
        self.detail = detail
        super().__init__(f"{error_type}: {detail or model_id}")


# ---------------------------------------------------------------------------
# Provider base + registry
# ---------------------------------------------------------------------------


class JudgeProvider(ABC):
    name: str = "abstract"

    @abstractmethod
    async def evaluate(self, model: JudgeModelSpec, request: JudgeRequest) -> JudgeResponse:
        ...


_PROVIDERS: dict[str, JudgeProvider] = {}


def register_provider(provider: JudgeProvider) -> None:
    _PROVIDERS[provider.name] = provider


def get_provider(name: str) -> JudgeProvider | None:
    return _PROVIDERS.get(name)


# ---------------------------------------------------------------------------
# Mock provider — deterministic so tests are stable
# ---------------------------------------------------------------------------


class MockJudgeProvider(JudgeProvider):
    """Hash-based scoring that varies across models *and* across rubrics
    but is identical across runs of the same input. Useful as the
    deployable default and as a contract the OpenAI adapter must honour
    in tests."""

    name = "mock"

    async def evaluate(self, model: JudgeModelSpec, request: JudgeRequest) -> JudgeResponse:
        seed = (
            f"{model.id}|{request.rubric_id}|{request.prompt}|"
            f"{request.system_prompt or ''}|{request.user_message or ''}|"
            f"{json.dumps(request.context, sort_keys=True, default=str)}"
        )
        digest = hashlib.sha256(seed.encode("utf-8")).digest()
        # Map first 8 bytes to a [0, 1] float; rest informs reason text.
        n = int.from_bytes(digest[:8], "big") / float(1 << 64)
        # Skew slightly toward the high end so demos look realistic but
        # still surface failures.
        score = round(min(max(0.5 + (n - 0.5) * 1.4, 0.0), 1.0), 3)
        verdict = "pass" if score >= 0.7 else "warn" if score >= 0.4 else "fail"
        reason = _mock_reason(request.rubric_id, model.name, score, request.context)
        _um = request.user_message or ""
        in_tokens = max(64, (len(request.prompt) + len(_um)) // 4)
        out_tokens = max(32, int(80 + 60 * (1 - score)))
        cost = (
            in_tokens / 1000.0 * model.cost_per_1k_input
            + out_tokens / 1000.0 * model.cost_per_1k_output
        )
        return JudgeResponse(
            score=score,
            verdict=verdict,
            reason=reason,
            input_tokens=in_tokens,
            output_tokens=out_tokens,
            cost_usd=round(cost, 6),
            raw={"provider": "mock", "model_id": model.id},
        )


def _mock_reason(rubric: str, model: str, score: float, context: dict[str, Any]) -> str:
    """Stable, human-readable justification — useful when testing UI states."""
    if score >= 0.85:
        verdict = "Strong"
    elif score >= 0.7:
        verdict = "Acceptable"
    elif score >= 0.4:
        verdict = "Borderline"
    else:
        verdict = "Weak"
    snippet = (context.get("response") or context.get("query") or "")[:120].replace("\n", " ")
    return f"[{model}] {verdict} on {rubric}: {snippet or 'no excerpt available'}"


# ---------------------------------------------------------------------------
# Optional OpenAI provider — only imported when configured
# ---------------------------------------------------------------------------


class OpenAIJudgeProvider(JudgeProvider):
    """Thin adapter to OpenAI's chat completions API.

    Two failure modes are handled distinctly so the runner can apply the
    12 §4 policy:

    - **Configuration gaps** (no API key, SDK missing) keep the legacy
      *degrade to mock* behaviour so demos in air-gapped envs still
      produce a number.
    - **Runtime call failures** (HTTP errors, parse errors) raise
      :class:`JudgeProviderError` so the runner can mark the row as
      ``verdict='error'`` and exclude it from aggregates instead of
      silently substituting a mock score that contaminates the run."""

    name = "openai"

    def __init__(self, api_key_env: str = "OPENAI_API_KEY") -> None:
        self._api_key_env = api_key_env

    async def evaluate(self, model: JudgeModelSpec, request: JudgeRequest) -> JudgeResponse:
        key_env = str(model.connection.get("api_key_env") or self._api_key_env)
        api_key = os.environ.get(key_env)
        if not api_key:
            _log.warning(
                "openai provider missing api key (%s); falling back to mock",
                key_env,
            )
            return await MockJudgeProvider().evaluate(model, request)
        try:
            from openai import AsyncOpenAI  # type: ignore
        except Exception:
            _log.warning("openai sdk not installed; falling back to mock")
            return await MockJudgeProvider().evaluate(model, request)
        base_raw = model.connection.get("base_url")
        base_url = str(base_raw).strip() if base_raw else None
        client_kw: dict[str, str] = {"api_key": api_key}
        if base_url:
            client_kw["base_url"] = base_url
        client = AsyncOpenAI(**client_kw)
        from easyobs.eval.judge.defaults import DEFAULT_JUDGE_SYSTEM_PROMPT

        sys_prompt = (request.system_prompt or "").strip() or DEFAULT_JUDGE_SYSTEM_PROMPT
        user_payload = _build_user_payload(model, request)
        try:
            resp = await client.chat.completions.create(
                model=model.model or model.id,
                temperature=model.temperature,
                max_tokens=request.max_tokens,
                messages=[
                    {"role": "system", "content": sys_prompt},
                    {"role": "user", "content": user_payload},
                ],
                response_format={"type": "json_object"},
            )
        except Exception as exc:
            _log.warning("openai judge call failed: %s", exc)
            raise JudgeProviderError(
                _classify_openai_error(exc),
                model_id=model.id,
                detail=str(exc)[:200],
            ) from exc

        return _parse_openai_response(resp, model, "openai")


def _classify_openai_error(exc: Exception) -> str:
    """Map a raw OpenAI / httpx exception to a JudgeErrorType slug. We
    keep this string-only so the providers module does not import the
    eval.types enum (avoids a cycle at package-init time)."""

    name = exc.__class__.__name__.lower()
    msg = str(exc).lower()
    if "timeout" in name or "timeout" in msg:
        return "timeout"
    if "ratelimit" in name or "rate_limit" in msg or "429" in msg:
        return "rate_limit"
    if "auth" in name or "401" in msg or "403" in msg or "permission" in msg:
        return "auth_error"
    if "5" in msg and ("0" in msg or "server" in msg):
        return "server_error"
    return "unknown"


class OnPremOpenAICompatibleProvider(OpenAIJudgeProvider):
    """vLLM / LiteLLM proxy / any OpenAI-compatible HTTP surface (air-gapped)."""

    name = "onprem_openai_compatible"

    async def evaluate(self, model: JudgeModelSpec, request: JudgeRequest) -> JudgeResponse:
        if not str(model.connection.get("base_url") or "").strip():
            _log.warning(
                "onprem_openai_compatible requires connection.base_url; using mock",
            )
            return await MockJudgeProvider().evaluate(model, request)
        return await OpenAIJudgeProvider.evaluate(self, model, request)


class AzureOpenAIJudgeProvider(JudgeProvider):
    """Azure OpenAI Service via the openai SDK's AzureOpenAI client."""

    name = "azure_openai"

    async def evaluate(self, model: JudgeModelSpec, request: JudgeRequest) -> JudgeResponse:
        key_env = str(model.connection.get("api_key_env") or "AZURE_OPENAI_API_KEY")
        api_key = os.environ.get(key_env)
        if not api_key:
            _log.warning("azure_openai provider missing api key (%s); falling back to mock", key_env)
            return await MockJudgeProvider().evaluate(model, request)
        try:
            from openai import AsyncAzureOpenAI  # type: ignore
        except Exception:
            _log.warning("openai sdk not installed; falling back to mock")
            return await MockJudgeProvider().evaluate(model, request)

        endpoint = str(model.connection.get("endpoint") or "").strip()
        deployment = str(model.connection.get("deployment") or model.model or "").strip()
        api_version = str(model.connection.get("api_version") or "2024-02-15-preview").strip()

        if not endpoint:
            _log.warning("azure_openai requires connection.endpoint; falling back to mock")
            return await MockJudgeProvider().evaluate(model, request)

        client = AsyncAzureOpenAI(
            api_key=api_key,
            azure_endpoint=endpoint,
            api_version=api_version,
        )

        from easyobs.eval.judge.defaults import DEFAULT_JUDGE_SYSTEM_PROMPT

        sys_prompt = (request.system_prompt or "").strip() or DEFAULT_JUDGE_SYSTEM_PROMPT
        user_payload = _build_user_payload(model, request)

        try:
            resp = await client.chat.completions.create(
                model=deployment,
                temperature=model.temperature,
                max_tokens=request.max_tokens,
                messages=[
                    {"role": "system", "content": sys_prompt},
                    {"role": "user", "content": user_payload},
                ],
                response_format={"type": "json_object"},
            )
        except Exception as exc:
            _log.warning("azure_openai judge call failed: %s", exc)
            raise JudgeProviderError(
                _classify_openai_error(exc),
                model_id=model.id,
                detail=str(exc)[:200],
            ) from exc

        return _parse_openai_response(resp, model, "azure_openai")


# ---------------------------------------------------------------------------
# Anthropic (Claude) provider
# ---------------------------------------------------------------------------


class AnthropicJudgeProvider(JudgeProvider):
    """Anthropic Claude Messages API adapter."""

    name = "anthropic"

    async def evaluate(self, model: JudgeModelSpec, request: JudgeRequest) -> JudgeResponse:
        key_env = str(model.connection.get("api_key_env") or "ANTHROPIC_API_KEY")
        api_key = os.environ.get(key_env)
        if not api_key:
            _log.warning("anthropic provider missing api key (%s); falling back to mock", key_env)
            return await MockJudgeProvider().evaluate(model, request)
        try:
            from anthropic import AsyncAnthropic  # type: ignore
        except Exception:
            _log.warning("anthropic sdk not installed; falling back to mock")
            return await MockJudgeProvider().evaluate(model, request)

        client = AsyncAnthropic(api_key=api_key)
        from easyobs.eval.judge.defaults import DEFAULT_JUDGE_SYSTEM_PROMPT

        sys_prompt = (request.system_prompt or "").strip() or DEFAULT_JUDGE_SYSTEM_PROMPT
        user_payload = _build_user_payload(model, request)

        try:
            resp = await client.messages.create(
                model=model.model or "claude-sonnet-4-20250514",
                max_tokens=request.max_tokens,
                temperature=model.temperature,
                system=sys_prompt,
                messages=[{"role": "user", "content": user_payload}],
            )
        except Exception as exc:
            _log.warning("anthropic judge call failed: %s", exc)
            raise JudgeProviderError(
                _classify_anthropic_error(exc),
                model_id=model.id,
                detail=str(exc)[:200],
            ) from exc

        content = ""
        for block in resp.content:
            if hasattr(block, "text"):
                content += block.text
        in_tokens = getattr(resp.usage, "input_tokens", 0) or 0
        out_tokens = getattr(resp.usage, "output_tokens", 0) or 0
        cost = (
            in_tokens / 1000.0 * model.cost_per_1k_input
            + out_tokens / 1000.0 * model.cost_per_1k_output
        )
        return _parse_json_content(content, model, "anthropic", in_tokens, out_tokens, cost)


def _classify_anthropic_error(exc: Exception) -> str:
    name = exc.__class__.__name__.lower()
    msg = str(exc).lower()
    if "timeout" in name or "timeout" in msg:
        return "timeout"
    if "ratelimit" in name or "rate_limit" in msg or "429" in msg:
        return "rate_limit"
    if "auth" in name or "401" in msg or "403" in msg or "permission" in msg:
        return "auth_error"
    if "overloaded" in msg or "529" in msg:
        return "server_error"
    return "unknown"


# ---------------------------------------------------------------------------
# Google Gemini (AI Studio) provider
# ---------------------------------------------------------------------------


class GoogleGeminiJudgeProvider(JudgeProvider):
    """Google AI Studio (Gemini) via the google-genai SDK."""

    name = "google_gemini"

    async def evaluate(self, model: JudgeModelSpec, request: JudgeRequest) -> JudgeResponse:
        key_env = str(model.connection.get("api_key_env") or "GOOGLE_API_KEY")
        api_key = os.environ.get(key_env)
        if not api_key:
            _log.warning("google_gemini provider missing api key (%s); falling back to mock", key_env)
            return await MockJudgeProvider().evaluate(model, request)
        try:
            from google import genai  # type: ignore
            from google.genai import types  # type: ignore
        except Exception:
            _log.warning("google-genai sdk not installed; falling back to mock")
            return await MockJudgeProvider().evaluate(model, request)

        client = genai.Client(api_key=api_key)
        from easyobs.eval.judge.defaults import DEFAULT_JUDGE_SYSTEM_PROMPT

        sys_prompt = (request.system_prompt or "").strip() or DEFAULT_JUDGE_SYSTEM_PROMPT
        user_payload = _build_user_payload(model, request)

        model_name = model.model or "gemini-2.0-flash"

        try:
            resp = await client.aio.models.generate_content(
                model=model_name,
                contents=user_payload,
                config=types.GenerateContentConfig(
                    system_instruction=sys_prompt,
                    temperature=model.temperature,
                    max_output_tokens=request.max_tokens,
                    response_mime_type="application/json",
                ),
            )
        except Exception as exc:
            _log.warning("google_gemini judge call failed: %s", exc)
            raise JudgeProviderError(
                _classify_google_error(exc),
                model_id=model.id,
                detail=str(exc)[:200],
            ) from exc

        content = resp.text or "{}"
        usage_meta = getattr(resp, "usage_metadata", None)
        in_tokens = int(getattr(usage_meta, "prompt_token_count", 0) or 0)
        out_tokens = int(getattr(usage_meta, "candidates_token_count", 0) or 0)
        cost = (
            in_tokens / 1000.0 * model.cost_per_1k_input
            + out_tokens / 1000.0 * model.cost_per_1k_output
        )
        return _parse_json_content(content, model, "google_gemini", in_tokens, out_tokens, cost)


def _classify_google_error(exc: Exception) -> str:
    msg = str(exc).lower()
    if "timeout" in msg:
        return "timeout"
    if "429" in msg or "resource_exhausted" in msg or "quota" in msg:
        return "rate_limit"
    if "403" in msg or "401" in msg or "permission" in msg:
        return "auth_error"
    if "500" in msg or "503" in msg or "internal" in msg:
        return "server_error"
    return "unknown"


# ---------------------------------------------------------------------------
# Google Vertex AI provider
# ---------------------------------------------------------------------------


class GoogleVertexJudgeProvider(JudgeProvider):
    """GCP Vertex AI (Gemini) using the google-cloud-aiplatform SDK."""

    name = "google_vertex"

    async def evaluate(self, model: JudgeModelSpec, request: JudgeRequest) -> JudgeResponse:
        cred_env = str(model.connection.get("api_key_env") or "GOOGLE_APPLICATION_CREDENTIALS")
        cred_path = os.environ.get(cred_env)
        if not cred_path:
            _log.warning(
                "google_vertex provider missing credentials env (%s); falling back to mock",
                cred_env,
            )
            return await MockJudgeProvider().evaluate(model, request)
        try:
            from vertexai.generative_models import GenerativeModel, GenerationConfig  # type: ignore
            import vertexai  # type: ignore
        except Exception:
            _log.warning("google-cloud-aiplatform sdk not installed; falling back to mock")
            return await MockJudgeProvider().evaluate(model, request)

        project_id = str(model.connection.get("project_id") or "").strip()
        location = str(model.connection.get("location") or "us-central1").strip()

        if not project_id:
            _log.warning("google_vertex requires connection.project_id; falling back to mock")
            return await MockJudgeProvider().evaluate(model, request)

        vertexai.init(project=project_id, location=location)
        model_name = model.model or "gemini-2.0-flash"

        from easyobs.eval.judge.defaults import DEFAULT_JUDGE_SYSTEM_PROMPT

        sys_prompt = (request.system_prompt or "").strip() or DEFAULT_JUDGE_SYSTEM_PROMPT
        user_payload = _build_user_payload(model, request)

        gen_model = GenerativeModel(
            model_name,
            system_instruction=sys_prompt,
        )
        gen_config = GenerationConfig(
            temperature=model.temperature,
            max_output_tokens=request.max_tokens,
            response_mime_type="application/json",
        )

        try:
            resp = await gen_model.generate_content_async(
                user_payload,
                generation_config=gen_config,
            )
        except Exception as exc:
            _log.warning("google_vertex judge call failed: %s", exc)
            raise JudgeProviderError(
                _classify_google_error(exc),
                model_id=model.id,
                detail=str(exc)[:200],
            ) from exc

        content = resp.text or "{}"
        usage_meta = getattr(resp, "usage_metadata", None)
        in_tokens = int(getattr(usage_meta, "prompt_token_count", 0) or 0)
        out_tokens = int(getattr(usage_meta, "candidates_token_count", 0) or 0)
        cost = (
            in_tokens / 1000.0 * model.cost_per_1k_input
            + out_tokens / 1000.0 * model.cost_per_1k_output
        )
        return _parse_json_content(content, model, "google_vertex", in_tokens, out_tokens, cost)


# ---------------------------------------------------------------------------
# AWS Bedrock provider
# ---------------------------------------------------------------------------


class AWSBedrockJudgeProvider(JudgeProvider):
    """AWS Bedrock Converse API adapter using boto3."""

    name = "aws_bedrock"

    async def evaluate(self, model: JudgeModelSpec, request: JudgeRequest) -> JudgeResponse:
        import asyncio

        cred_env = str(model.connection.get("credential_env_hint") or "AWS_PROFILE")
        region = str(model.connection.get("aws_region") or "us-east-1").strip()

        profile_name = os.environ.get(cred_env) if cred_env == "AWS_PROFILE" else None
        access_key = os.environ.get("AWS_ACCESS_KEY_ID")
        # Either a named profile or explicit keys must be available
        if not profile_name and not access_key:
            _log.warning(
                "aws_bedrock provider: neither %s nor AWS_ACCESS_KEY_ID set; falling back to mock",
                cred_env,
            )
            return await MockJudgeProvider().evaluate(model, request)

        try:
            import boto3  # type: ignore
        except Exception:
            _log.warning("boto3 not installed; falling back to mock")
            return await MockJudgeProvider().evaluate(model, request)

        session_kw: dict[str, str] = {"region_name": region}
        if profile_name:
            session_kw["profile_name"] = profile_name

        session = boto3.Session(**session_kw)
        client = session.client("bedrock-runtime", region_name=region)

        from easyobs.eval.judge.defaults import DEFAULT_JUDGE_SYSTEM_PROMPT

        sys_prompt = (request.system_prompt or "").strip() or DEFAULT_JUDGE_SYSTEM_PROMPT
        user_payload = _build_user_payload(model, request)
        model_id = model.model or "anthropic.claude-3-haiku-20240307-v1:0"

        try:
            resp = await asyncio.to_thread(
                client.converse,
                modelId=model_id,
                system=[{"text": sys_prompt}],
                messages=[
                    {"role": "user", "content": [{"text": user_payload}]},
                ],
                inferenceConfig={
                    "temperature": model.temperature,
                    "maxTokens": request.max_tokens,
                },
            )
        except Exception as exc:
            _log.warning("aws_bedrock judge call failed: %s", exc)
            raise JudgeProviderError(
                _classify_bedrock_error(exc),
                model_id=model.id,
                detail=str(exc)[:200],
            ) from exc

        output = resp.get("output", {})
        message = output.get("message", {})
        content_blocks = message.get("content", [])
        content = ""
        for block in content_blocks:
            if "text" in block:
                content += block["text"]

        # Bedrock models (especially Claude) often wrap JSON in markdown fences.
        # Strip them inline before passing to the shared parser as a safety net.
        _c = content.strip()
        if _c.startswith("```"):
            _lines = _c.split("\n", 1)
            _c = _lines[1] if len(_lines) > 1 else _c[3:]
            if _c.rstrip().endswith("```"):
                _c = _c.rstrip()[:-3]
            content = _c.strip()

        usage = resp.get("usage", {})
        in_tokens = int(usage.get("inputTokens", 0))
        out_tokens = int(usage.get("outputTokens", 0))
        cost = (
            in_tokens / 1000.0 * model.cost_per_1k_input
            + out_tokens / 1000.0 * model.cost_per_1k_output
        )
        return _parse_json_content(content, model, "aws_bedrock", in_tokens, out_tokens, cost)


def _classify_bedrock_error(exc: Exception) -> str:
    name = exc.__class__.__name__.lower()
    msg = str(exc).lower()
    if "timeout" in name or "timeout" in msg:
        return "timeout"
    if "throttling" in name or "throttling" in msg or "429" in msg:
        return "rate_limit"
    if "accessdenied" in name or "403" in msg or "credential" in msg or "not authorized" in msg:
        return "auth_error"
    if "500" in msg or "internalserver" in name or "serviceexception" in name:
        return "server_error"
    if "validationexception" in name or "validation" in msg:
        return "unknown"
    return "unknown"


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _build_user_payload(model: JudgeModelSpec, request: JudgeRequest) -> str:
    """Build the user message content (shared across providers)."""
    if request.user_message is not None:
        return request.user_message
    return json.dumps(
        {
            "rubric_id": request.rubric_id,
            "rubric": request.prompt,
            "context": request.context,
        },
        ensure_ascii=False,
        default=str,
    )


def _parse_openai_response(resp: Any, model: JudgeModelSpec, provider_name: str) -> JudgeResponse:
    """Parse an OpenAI-style chat completion response."""
    choice = (resp.choices or [None])[0]
    content = (choice.message.content if choice and choice.message else None) or "{}"
    usage = getattr(resp, "usage", None)
    in_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
    out_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
    cost = (
        in_tokens / 1000.0 * model.cost_per_1k_input
        + out_tokens / 1000.0 * model.cost_per_1k_output
    )
    return _parse_json_content(content, model, provider_name, in_tokens, out_tokens, cost)


def _strip_markdown_fences(text: str) -> str:
    """Remove markdown code fences (```json ... ```) that some models wrap around JSON.

    Handles both complete fences and truncated responses where the closing
    fence is missing (e.g. due to max_tokens cutoff).
    """
    import re

    stripped = text.strip()
    # Complete fence: ```json ... ```
    m = re.match(r"^```(?:json)?\s*\n?(.*?)```\s*$", stripped, re.DOTALL)
    if m:
        return m.group(1).strip()
    # Truncated fence: opening ```json but no closing ``` (max_tokens cutoff)
    m = re.match(r"^```(?:json)?\s*\n?(.*)", stripped, re.DOTALL)
    if m:
        return m.group(1).strip()
    return stripped


def _try_salvage_truncated_json(text: str) -> dict[str, Any] | None:
    """Best-effort extraction of score/verdict/reason from truncated JSON.

    When max_tokens cuts the response mid-JSON, we try to extract whatever
    fields were already completed. Returns None if nothing useful found.
    """
    import re

    result: dict[str, Any] = {}
    score_m = re.search(r'"score"\s*:\s*([\d.]+)', text)
    if score_m:
        try:
            result["score"] = float(score_m.group(1))
        except ValueError:
            pass
    verdict_m = re.search(r'"verdict"\s*:\s*"([^"]*)"', text)
    if verdict_m:
        result["verdict"] = verdict_m.group(1)
    reason_m = re.search(r'"reason"\s*:\s*"((?:[^"\\]|\\.)*)', text)
    if reason_m:
        result["reason"] = reason_m.group(1)
    if "score" in result:
        return result
    return None


def _parse_json_content(
    content: str,
    model: JudgeModelSpec,
    provider_name: str,
    in_tokens: int,
    out_tokens: int,
    cost: float,
) -> JudgeResponse:
    """Parse JSON response content into a JudgeResponse."""
    # Primary fence removal (no regex dependency for robustness)
    cleaned = content.strip()
    if cleaned.startswith("```"):
        lines = cleaned.split("\n", 1)
        cleaned = lines[1] if len(lines) > 1 else cleaned[3:]
        if cleaned.rstrip().endswith("```"):
            cleaned = cleaned.rstrip()[:-3]
        cleaned = cleaned.strip()

    try:
        parsed = json.loads(cleaned)
    except Exception:
        # Attempt to salvage truncated JSON (e.g. max_tokens cutoff)
        parsed = _try_salvage_truncated_json(cleaned)
        if parsed is None:
            raise JudgeProviderError(
                "parse_error",
                model_id=model.id,
                detail=f"judge returned non-JSON: {content[:120]!r}",
            )

    try:
        score = float(parsed.get("score") or 0.0)
    except Exception:
        score = 0.0
    if math.isnan(score):
        score = 0.0
    score = max(0.0, min(1.0, score))
    verdict = str(parsed.get("verdict") or "warn")
    reason = str(parsed.get("reason") or "")
    return JudgeResponse(
        score=score,
        verdict=verdict,
        reason=reason[:400],
        input_tokens=in_tokens,
        output_tokens=out_tokens,
        cost_usd=round(cost, 6),
        raw={"provider": provider_name, "model_id": model.id},
    )


# ---------------------------------------------------------------------------
# Provider registration
# ---------------------------------------------------------------------------

register_provider(MockJudgeProvider())
register_provider(OpenAIJudgeProvider())
register_provider(OnPremOpenAICompatibleProvider())
register_provider(AzureOpenAIJudgeProvider())
register_provider(AnthropicJudgeProvider())
register_provider(GoogleGeminiJudgeProvider())
register_provider(GoogleVertexJudgeProvider())
register_provider(AWSBedrockJudgeProvider())

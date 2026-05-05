"""LLM-as-a-Judge runner with multi-judge consensus.

The judge runner is intentionally provider-agnostic: each registered
:class:`JudgeProvider` receives a normalised prompt and returns a
``JudgeResponse``. The default deployment ships with the deterministic
``mock`` provider so the test suite (and air-gapped demos) work without an
API key. Operators can plug in real providers (OpenAI, Anthropic, Bedrock,
…) without touching the orchestrator.
"""

from easyobs.eval.judge.consensus import aggregate_consensus
from easyobs.eval.judge.providers import (
    JudgeModelSpec,
    JudgeProvider,
    JudgeRequest,
    JudgeResponse,
    MockJudgeProvider,
    OpenAIJudgeProvider,
    OnPremOpenAICompatibleProvider,
    AzureOpenAIJudgeProvider,
    AnthropicJudgeProvider,
    GoogleGeminiJudgeProvider,
    GoogleVertexJudgeProvider,
    AWSBedrockJudgeProvider,
    get_provider,
    register_provider,
)
from easyobs.eval.judge.runner import JudgeOutcome, run_judges

__all__ = [
    "aggregate_consensus",
    "JudgeModelSpec",
    "JudgeProvider",
    "JudgeRequest",
    "JudgeResponse",
    "MockJudgeProvider",
    "OpenAIJudgeProvider",
    "OnPremOpenAICompatibleProvider",
    "AzureOpenAIJudgeProvider",
    "AnthropicJudgeProvider",
    "GoogleGeminiJudgeProvider",
    "GoogleVertexJudgeProvider",
    "AWSBedrockJudgeProvider",
    "get_provider",
    "register_provider",
    "JudgeOutcome",
    "run_judges",
]

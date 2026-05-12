# easyobs-agent

Lightweight LLM/Agent observability SDK built on OpenTelemetry.

Install `easyobs-agent` in your agent service to send spans to the EasyObs console with minimal boilerplate.

## Installation

```bash
pip install easyobs-agent
```

This installs all required dependencies including `langchain-core`, so LangChain auto-tracing works out of the box.

## Quick Start

```python
from easyobs_agent import init, traced, record_llm

# 1. Connect to EasyObs server
init(
    "http://127.0.0.1:8787",
    token="eobs_your_ingest_token",
    service="my-agent",
)

# 2. Decorate functions with @traced
@traced
def answer(question: str) -> str:
    result = call_llm(question)

    # 3. Record LLM metadata
    record_llm(model="gpt-4o-mini", tokens_in=120, tokens_out=80)
    return result
```

## Features

| Feature | Description |
|---------|-------------|
| `init()` | Configure OTLP/HTTP exporter and connect to EasyObs |
| `@traced` | Auto-create OTel span per function call (sync/async) |
| `span_block()` | Context manager for child spans |
| `span_tag()` | Attach attributes to the current span |
| `record_llm()` | Record LLM call metadata (model, tokens, cost) |
| `record_retrieval()` | Record RAG/retrieval results |
| `record_tool()` | Record tool invocations |
| `record_session()` | Attach session/user/request IDs |
| `EasyObsCallbackHandler` | LangChain callback handler (auto-tracing) |

## Auto LangChain Tracing

```python
from easyobs_agent import init

# auto=True enables both capture_io and auto_langchain
init(
    "http://127.0.0.1:8787",
    token="eobs_…",
    service="rag-bot",
    auto=True,
)

# All LangChain invocations are automatically captured — no callbacks needed
from langchain_openai import ChatOpenAI
llm = ChatOpenAI(model="gpt-4o-mini")
llm.invoke("Hello")  # span created automatically
```

## Advanced Usage

### Selective I/O Capture

```python
@traced(capture=True)   # force capture for this function only
def sensitive_fn(query: str) -> str: ...

@traced(capture=False)  # opt-out regardless of global setting
def secret_fn(api_key: str) -> str: ...
```

### Manual Span Blocks

```python
from easyobs_agent import span_block, record_retrieval

with span_block("search", kind="retrieve") as span:
    docs = vector_store.search(query)
    record_retrieval(query=query, docs=docs)
```

## Requirements

- Python >= 3.9
- OpenTelemetry SDK >= 1.28.0
- langchain-core >= 0.3.0

## License

MIT

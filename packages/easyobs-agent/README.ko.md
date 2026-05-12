# easyobs-agent

**영문 원문:** [`README.md`](README.md)

OpenTelemetry 기반의 경량 LLM/에이전트 관측(Observability) SDK입니다.

에이전트 서비스에 `easyobs-agent`를 설치하면 최소한의 보일러플레이트로 EasyObs 콘솔에 스팬을 보낼 수 있습니다.

## 설치

```bash
pip install easyobs-agent
```

LangChain 자동 추적을 위해 `langchain-core`를 포함한 필요한 의존성이 모두 설치됩니다.

## 빠른 시작

```python
from easyobs_agent import init, traced, record_llm

# 1. EasyObs 서버에 연결
init(
    "http://127.0.0.1:8787",
    token="eobs_your_ingest_token",
    service="my-agent",
)

# 2. 함수에 @traced 적용
@traced
def answer(question: str) -> str:
    result = call_llm(question)

    # 3. LLM 메타데이터 기록
    record_llm(model="gpt-4o-mini", tokens_in=120, tokens_out=80)
    return result
```

## 기능

| 기능 | 설명 |
|------|------|
| `init()` | OTLP/HTTP 익스포터 설정 및 EasyObs 연결 |
| `@traced` | 함수 호출마다 OTel 스팬 자동 생성(동기/비동기) |
| `span_block()` | 자식 스팬용 컨텍스트 매니저 |
| `span_tag()` | 현재 스팬에 속성 부착 |
| `record_llm()` | LLM 호출 메타데이터 기록(모델, 토큰, 비용) |
| `record_retrieval()` | RAG/검색 결과 기록 |
| `record_tool()` | 도구 호출 기록 |
| `record_session()` | 세션/사용자/요청 ID 부착 |
| `EasyObsCallbackHandler` | LangChain 콜백 핸들러(자동 추적) |

## LangChain 자동 추적

```python
from easyobs_agent import init

# auto=True 는 capture_io와 auto_langchain을 모두 켭니다
init(
    "http://127.0.0.1:8787",
    token="eobs_…",
    service="rag-bot",
    auto=True,
)

# LangChain 호출이 자동으로 수집됩니다 — 별도 콜백 불필요
from langchain_openai import ChatOpenAI
llm = ChatOpenAI(model="gpt-4o-mini")
llm.invoke("Hello")  # 스팬 자동 생성
```

## 고급 사용

### 선택적 I/O 캡처

```python
@traced(capture=True)   # 이 함수만 강제 캡처
def sensitive_fn(query: str) -> str: ...

@traced(capture=False)  # 전역 설정과 무관하게 옵트아웃
def secret_fn(api_key: str) -> str: ...
```

### 수동 스팬 블록

```python
from easyobs_agent import span_block, record_retrieval

with span_block("search", kind="retrieve") as span:
    docs = vector_store.search(query)
    record_retrieval(query=query, docs=docs)
```

## 요구 사항

- Python >= 3.9
- OpenTelemetry SDK >= 1.28.0
- langchain-core >= 0.3.0

## 라이선스

MIT

## PyPI 배포

유지보수자용 절차는 [`PUBLISHING.ko.md`](PUBLISHING.ko.md)를 참고하세요.

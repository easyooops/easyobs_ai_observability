# EasyObs

**AI Observability & Evaluation 플랫폼** — 모든 LLM 호출을 추적하고, 출력 품질을 대규모로 평가하며, 신뢰할 수 있는 AI 제품을 자신 있게 출시하세요.

OpenAPI · SQLite / Postgres · Object-first Blob (Local / S3 · Azure · GCS) · OTLP/HTTP ingest

**MIT License** 오픈소스 — 개인 프로젝트, 스타트업, 엔터프라이즈 모두 자유롭게 사용할 수 있습니다.

---

## 주요 기능

### 대시보드 & 메트릭

전체 AI 서비스의 요청 수, 토큰 사용량, 비용, 지연 시간을 한눈에 모니터링합니다.

![Metrics Overview](apps/web/public/images/metrics-overview-dashboard.png)

### 트레이싱

LLM 호출의 전체 라이프사이클을 추적하고, 개별 span 단위로 입력/출력/메타데이터를 검사합니다.

![Tracing Full Layout](apps/web/public/images/tracing-full-layout.png)

![Tracing Inspector](apps/web/public/images/tracing-inspector-summary.png)

### 품질 & 평가 (Evaluation)

Observability 스택에 직접 통합된 프로덕션 수준의 AI 평가 프레임워크. 별도 도구가 필요 없습니다.

**평가 프로필 (Evaluation Profiles)** — Rule 기반 검증, 멀티 LLM 심사위원 패널, 사람이 라벨링한 Golden Set을 하나의 평가 프로필로 결합합니다. 요청 시 실행하거나, 모든 ingest에 자동으로 실행되도록 설정할 수 있습니다.

![Quality Overview](apps/web/public/images/eval-quality-overview.png)

![Eval Profiles](apps/web/public/images/eval-profiles.png)

**멀티 LLM 심사위원 합의 (Multi-LLM Judge Consensus)** — 여러 LLM 프로바이더(OpenAI, Anthropic, Google)를 심사위원으로 활용하고, 합의 정책(다수결, 만장일치, 가중치)을 설정합니다. 내장 비용 가드가 실행별/주제별/월별 예산 한도로 비용 초과를 방지합니다.

![Eval Judges](apps/web/public/images/eval-judges-page.png)

**평가 실행 (Evaluation Runs)** — 라이브 트레이스 또는 Golden Set에 대해 평가를 실행합니다. 합격/불합격률, 점수 분포, 평가 실행당 비용을 추적하고, 시간별 비교로 품질 회귀를 감지합니다.

![Eval Runs](apps/web/public/images/eval-runs-launch-source-cards.png)

![Eval Run Detail](apps/web/public/images/eval-runs-detail-summary.png)

**Golden Set** — 수동 라벨, 생성된 후보, 트레이스 캡처로 Ground-truth 데이터셋을 구축합니다. UI(Excel/CSV) 또는 API로 업로드하며, 회귀 테스트와 심사위원 보정에 활용합니다.

![Golden Set Detail](apps/web/public/images/eval-golden-sets-detail.png)

**개선 추천 (Improvement Recommendations)** — 낮은 점수는 자동으로 카탈로그화된 메트릭 및 카테고리에 매핑되며, 노력도 힌트가 함께 제공되어 팀이 무엇을 어떤 난이도로 수정해야 하는지 즉시 파악할 수 있습니다.

![Improvements](apps/web/public/images/eval-improvements-page.png)

| 기능 | 설명 |
|------|------|
| 인제스트 시 자동 규칙 | 모든 수신 트레이스에 fire-and-forget 규칙 평가 실행 |
| 멀티 프로바이더 심사위원 | OpenAI, Anthropic, Google Gemini, AWS Bedrock |
| 합의 정책 | 다수결, 만장일치, 가중치, 커스텀 임계값 |
| 비용 가드 | 실행별 / 주제별 / 월별 예산 한도 |
| Golden Set 관리 | 수동, 생성, 트레이스 라벨링 방식 + Excel 업로드 |
| 개선 카탈로그 | 메트릭 → 카테고리 → 노력도 힌트로 실행 가능한 수정 안내 |

### 인터랙션 & 세션

사용자별 세션 및 상호작용 히스토리를 추적하여 사용 패턴을 분석합니다.

![Interactions & Sessions](apps/web/public/images/interactions-sessions-users.png)

### 알람 & 채널

이상 감지 시 알림을 설정하고 Slack, Webhook 등 다양한 채널로 전달합니다.

![Alarms & Channels](apps/web/public/images/alarms-channels.png)

### 조직 & 멤버 관리

멀티 테넌트 조직 관리와 역할 기반 접근 제어(RBAC)를 지원합니다.

![Organizations & Members](apps/web/public/images/organizations-members.png)

---

## 프로덕션 아키텍처

<p align="center">
  <img src="apps/web/public/images/architecture-production.png" alt="EasyObs Production Architecture" width="720"/>
</p>

단일 퍼블릭 진입점(Nginx, ALB, Traefik)이 브라우저 트래픽을 **Web Console**로, API 경로(`/v1`, `/otlp`, `/healthz`)를 **API Server**로 라우팅합니다.

**수평 확장 구조** — Web 티어(Next.js)와 API 티어(FastAPI/Uvicorn) 모두 무상태(stateless)입니다. 로드 밸런서 뒤에 인스턴스를 추가하는 것만으로 대규모 트랜잭션을 코드 변경 없이 처리할 수 있습니다.

**계층형 스토리지 전략:**

| 계층 | 스토리지 | 접근 패턴 |
|------|----------|-----------|
| **Hot** | 로컬 파일시스템 또는 Attached SSD | 실시간 인제스트, 당일 데이터 즉시 쿼리 |
| **Warm** | Blob Storage (Azure Blob, GCS) | 최근 이력 데이터, DuckDB를 통한 고속 분석 쿼리 |
| **Cold** | S3 (또는 S3 호환 스토리지) | 장기 보관, 비용 최적화; DuckDB가 httpfs로 직접 스캔 — ETL 불필요 |

DuckDB는 API 서버와 동일 프로세스에서 동작하며, 모든 계층의 Parquet 파일을 컬럼 프루닝 및 조건 푸시다운으로 스캔합니다. 별도 데이터 웨어하우스 없이 ClickHouse 수준의 분석 성능을 제공합니다.

---

## 빠른 시작

### 요구 사항

- Python **3.11+**
- Node.js **20+** 및 npm
- 선택: [uv](https://docs.astral.sh/uv/) (가상환경 관리)

### Windows (PowerShell)

```powershell
Copy-Item .env.sample .env
.\scripts\run-dev.ps1
```

### Linux / macOS

```bash
chmod +x scripts/run-dev.sh
cp .env.sample .env
./scripts/run-dev.sh
```

### URL

| 서비스 | URL |
|--------|-----|
| Web | http://localhost:3000 |
| API | http://127.0.0.1:8787 |
| OpenAPI | http://127.0.0.1:8787/docs |
| Health | http://127.0.0.1:8787/healthz |

![Quick Start Terminal](apps/web/public/images/quickstart-run-dev-terminal.png)

---

## 환경 변수

`.env.sample`을 `.env`로 복사하여 사용합니다. 주요 항목:

```bash
# 스토리지
EASYOBS_DATA_DIR=./data
EASYOBS_DATABASE_URL=                   # 비어있으면 → SQLite, 프로덕션: postgresql+asyncpg://...

# HTTP
EASYOBS_API_HOST=127.0.0.1
EASYOBS_API_PORT=8787

# 인증
EASYOBS_JWT_SECRET=                     # 비어있으면 → 자동 생성

# 프론트엔드
NEXT_PUBLIC_API_URL=http://127.0.0.1:8787

# 데모 시드 (최초 부팅 시만)
EASYOBS_SEED_MOCK_DATA=false
```

전체 목록: [`src/easyobs/settings.py`](src/easyobs/settings.py) | [`.env.sample`](.env.sample)

---

## `easyobs_agent` SDK

경량 클라이언트 (~15KB) + OpenTelemetry 의존성; OTLP/HTTP로 트레이스를 전송합니다.

```bash
pip install easyobs-agent
```

```python
from easyobs_agent import init, traced

init(
    "http://<easyobs-server>:8787",
    token="eobs_...",
    service="my-service",
)

@traced("my.operation")
def do_something(query: str) -> str:
    ...
```

---

## 오픈소스 출처

EasyObs는 아래 오픈소스 프로젝트들 위에 구축되었습니다. 감사합니다.

### Backend (Python)

| 프로젝트 | 라이센스 | 링크 |
|----------|----------|------|
| FastAPI | MIT | https://github.com/fastapi/fastapi |
| Uvicorn | BSD-3 | https://github.com/encode/uvicorn |
| SQLAlchemy | MIT | https://github.com/sqlalchemy/sqlalchemy |
| Pydantic | MIT | https://github.com/pydantic/pydantic |
| DuckDB | MIT | https://github.com/duckdb/duckdb |
| Polars | MIT | https://github.com/pola-rs/polars |
| PyArrow (Apache Arrow) | Apache-2.0 | https://github.com/apache/arrow |
| OpenTelemetry Python | Apache-2.0 | https://github.com/open-telemetry/opentelemetry-python |
| HTTPX | BSD-3 | https://github.com/encode/httpx |
| PyJWT | MIT | https://github.com/jpadilla/pyjwt |
| Argon2-cffi | MIT | https://github.com/hynek/argon2-cffi |
| openpyxl | MIT | https://github.com/theorchard/openpyxl |

### Frontend (TypeScript)

| 프로젝트 | 라이센스 | 링크 |
|----------|----------|------|
| Next.js | MIT | https://github.com/vercel/next.js |
| React | MIT | https://github.com/facebook/react |
| TanStack Query | MIT | https://github.com/TanStack/query |
| TypeScript | Apache-2.0 | https://github.com/microsoft/TypeScript |

### Cloud & LLM 연동

| 프로젝트 | 라이센스 | 링크 |
|----------|----------|------|
| Boto3 (AWS SDK) | Apache-2.0 | https://github.com/boto/boto3 |
| Azure Storage Blob | MIT | https://github.com/Azure/azure-sdk-for-python |
| Google Cloud Storage | Apache-2.0 | https://github.com/googleapis/python-storage |
| OpenAI Python | MIT | https://github.com/openai/openai-python |
| Anthropic Python | MIT | https://github.com/anthropics/anthropic-sdk-python |

---

## 라이센스

MIT License — 개인, 상업적, 엔터프라이즈 용도 모두 제한 없이 자유롭게 사용할 수 있습니다.

자세한 내용은 [`LICENSE`](./LICENSE) 파일을 참고하세요.

---

## 문의

**Suyeong Yoo** — ssu0416@gmail.com

---

프로덕션 배포: [`setup/README.md`](setup/README.md) 참고 (Terraform, Compose, 에어갭 환경).

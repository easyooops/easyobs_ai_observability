# EasyObs

**OpenAPI · SQLite(또는 Postgres) · 객체-우선(로컬 파일 / S3·Azure·GCS) · OTLP/HTTP** 기반의 경량 AI Observability 플랫폼입니다.

---

## 1. 실행 가이드

### 필요 조건

- Python **3.11+**
- Node.js **20+** 및 npm
- (선택) [uv](https://docs.astral.sh/uv/) — 가상환경 자동 관리

> 모든 명령은 이 저장소의 루트 디렉터리에서 실행하는 것을 기준으로 합니다.

### 1-1. Windows (PowerShell)

```powershell
# (최초 1회) .env 생성 — 기본 설정 그대로 가도 됩니다
Copy-Item .env.sample .env

# 한 번에 실행 (API + Web)
.\scripts\run-dev.ps1
```

옵션:

```powershell
.\scripts\run-dev.ps1 -ApiPort 8787 -WebPort 3000   # 포트 변경
.\scripts\run-dev.ps1 -SkipInstall                  # 의존성 설치 생략
.\scripts\run-dev.ps1 -LogFormat json -LogLevel DEBUG
```

스크립트는 API 서버를 백그라운드로 띄운 뒤 같은 터미널에서 Next.js dev 서버를
포그라운드로 실행하고, **Ctrl+C** 로 종료하면 두 프로세스를 함께 정리합니다.
API 로그는 `data/api.log` 와 현재 터미널에 동시에 흘러나옵니다.

데이터/세션을 처음부터 다시 시작하고 싶을 때:

```powershell
.\.venv\Scripts\easyobs.exe reset-data --force --yes
.\scripts\run-dev.ps1
```

### 1-2. Linux / macOS

```bash
# (최초 1회) 실행 권한 + .env 복사
chmod +x scripts/run-dev.sh
cp .env.sample .env

# 한 번에 실행 (API + Web)
./scripts/run-dev.sh
```

옵션 (환경 변수로 전달):

```bash
API_PORT=8787 WEB_PORT=3000 ./scripts/run-dev.sh   # 포트 변경
./scripts/run-dev.sh --skip-install                # 의존성 설치 생략
EASYOBS_LOG_FORMAT=json EASYOBS_LOG_LEVEL=DEBUG ./scripts/run-dev.sh
```

데이터 초기화:

```bash
./.venv/bin/easyobs reset-data --force --yes
./scripts/run-dev.sh
```

### 1-3. 접속

| 서비스 | URL |
|--------|-----|
| Web 콘솔 | http://localhost:3000 |
| API      | http://127.0.0.1:8787 |
| OpenAPI  | http://127.0.0.1:8787/docs |
| Health   | http://127.0.0.1:8787/healthz |

> 첫 가입자는 자동으로 슈퍼 어드민(SA)이 되며 기본 `administrator` 조직에 PO 로
> 추가됩니다. `.env` 의 `EASYOBS_SEED_MOCK_DATA=true` 가 켜져 있으면 첫 부팅 시
> 데모 트레이스 100건이 administrator/demo 서비스에 자동 시드됩니다.

---

## 2. 환경 변수 가이드

모든 변수는 선택 사항이며 기본값은 `src/easyobs/settings.py` 에 정의되어 있습니다.
`.env` 파일에 적거나, 컨테이너/서버리스 런타임의 진짜 환경 변수로 주입하세요.
완전한 주석이 달린 템플릿은 [`.env.sample`](.env.sample) 을 참고하세요.

### 2-1. 저장소

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `EASYOBS_DATA_DIR` | `./data` | SQLite 카탈로그, JWT 시크릿, 트레이스 blob 저장 디렉터리. `easyobs reset-data` 가 비우는 곳. 컨테이너에서는 마운트 볼륨 사용. |
| `EASYOBS_DATABASE_URL` | (자동) | 비워두면 `<DATA_DIR>/catalog.sqlite3` 로 자동 결정. 운영에서는 Postgres 등으로 교체 (`postgresql+asyncpg://user:pw@host:5432/easyobs`). |

### 2-2. HTTP 서버

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `EASYOBS_API_HOST` | `127.0.0.1` | FastAPI 프로세스 바인드 주소. 컨테이너 내부에서는 `0.0.0.0`. |
| `EASYOBS_API_PORT` | `8787` | API + OTLP/HTTP 수집 엔드포인트 포트. |
| `EASYOBS_CORS_ORIGINS` | `http://127.0.0.1:3000,http://localhost:3000` | 콤마로 구분된 브라우저 origin 화이트리스트. localhost 변형은 정규식으로 자동 매칭되므로 비-로컬 호스트일 때만 추가. |

### 2-3. 인증 / 세션

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `EASYOBS_JWT_SECRET` | (자동 생성) | HS256 세션 JWT 서명 키. 비워두면 첫 부팅 시 생성되어 `<DATA_DIR>/jwt.secret` 에 저장. 운영에서는 `openssl rand -hex 32` 등으로 강한 값을 주입. |
| `EASYOBS_JWT_TTL_HOURS` | `12` | JWT 만료 시간(시간). 만료 후 사용자는 재로그인. |

### 2-4. LLM 비용 단가

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `EASYOBS_PRICING_SOURCE` | `auto` | ingest 시 `o.price` 자동 채우기 소스. `auto` 는 `tokencost` → `litellm` → 내장 fallback 순서로 시도. 강제 고정: `tokencost` / `litellm` / `builtin`. |

### 2-5. 로깅 / 운영 가시성

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `EASYOBS_LOG_LEVEL` | `INFO` | `easyobs.*` + `uvicorn` 루트 레벨. `DEBUG` / `WARNING` / `ERROR`. |
| `EASYOBS_LOG_FORMAT` | `console` | 사람이 읽기 좋은 단일 라인. `json` 으로 바꾸면 한 줄당 JSON 1개 — CloudWatch Logs / Loki / Cloud Logging 등이 그대로 수집. |
| `EASYOBS_LOG_FILE` | (없음) | 지정 시 stdout 에 더해 해당 파일에도 미러링. 컨테이너/서버리스에서는 비워둬 플랫폼이 로그를 가져가게 하는 편이 좋음. |
| `EASYOBS_LOG_REQUEST_BODY` | `false` | true 면 요청 미들웨어가 JSON 본문(최대 4 KB)도 기록. PII/시크릿 유출 위험이 있어 기본 off. |

### 2-6. 데모 / Mock 데이터 (첫 부팅에만 동작)

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `EASYOBS_SEED_MOCK_DATA` | `false` | true 면 첫 부팅 시 `administrator` 조직 + `demo` 서비스를 보장하고 합성 트레이스를 자동 생성. **카탈로그에 트레이스가 1건이라도 있으면 무조건 스킵**하므로 실데이터를 덮을 수 없음. 다시 시드하려면 `easyobs reset-data --force --yes` 먼저. |
| `EASYOBS_SEED_MOCK_TRACES` | `100` | 위 옵션이 켜졌을 때 생성할 합성 트레이스 수. |
| `EASYOBS_SEED_MOCK_WINDOW_HOURS` | `24` | 합성 트레이스를 분산할 시간 윈도우(시간). |

### 2-7. 프론트엔드 (`apps/web`)

`apps/web/.env.local` 또는 빌드/런타임 환경 변수로 주입합니다.

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `NEXT_PUBLIC_API_URL` | `http://127.0.0.1:8787` | 콘솔이 호출할 API 베이스 URL. 운영에서는 동일 도메인의 리버스 프록시 경로 권장. |

### 2-8. 평가(Quality) 모듈 — feature flag

평가 설계를 구현한 모듈입니다. 운영 트레이스
파이프라인과 완전히 분리되어 있으며, 두 플래그로 단계적으로 끄고 켤 수 있습니다.

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `EASYOBS_EVAL_ENABLED` | `true` | false 로 두면 `/v1/evaluations/*` 라우터 자체가 등록되지 않고, 프런트의 **Quality** 메뉴는 안내 페이지만 노출. 운영 영향 0. |
| `EASYOBS_EVAL_AUTO_RULE_ON_INGEST` | `true` | 트레이스 ingest 직후 fire-and-forget 으로 rule 평가가 트리거됨. 실패해도 ingest 결과 자체에는 영향 없음(예외는 격리되어 로그로 흘러나옴). 부하 우려 시 false. |

---

## 3. 평가(Quality) 모듈 한눈에 보기

### 3-1. 핵심 차별점

- **3-Way 평가**: Rule-based(코드/DSL) · LLM-as-a-Judge(다중 모델 합의) · Human(GT
  라벨링)을 단일 *Profile* 로 결합해 같은 Run 안에서 결과를 정렬·비교.
- **Multi-Judge Consensus**: 하나의 평가 항목을 여러 LLM 에 위임하고
  `single` / `majority` / `unanimous` / `weighted` 4가지 합의 정책을 선택.
  각 Judge 가중치/온도/단가를 모두 명시.
- **Cost Governance**: 모든 Judge 호출이 token 단가 × 사용량으로 집계되며 Profile
  단위 `cost_guard` 로 *per-run* / *per-subject* / *monthly* 예산 초과 시 사전
  차단(`block`) 또는 다운그레이드(`downgrade`) / 알림(`notify`) 정책을 선택.
- **3-Entry Golden Sets**: 같은 골든 세트를 (a) 사람이 직접 작성, (b) 자동
  생성(트레이스 통계 기반 후보 추천), (c) 트레이스 결과에 GT 라벨링 — 세 경로로
  채울 수 있도록 API/UI 분리.
- **Improvement Pack (52 × N + Effort)**: 점수 낮은 Run 결과만 모아 *52종 평가
  메트릭 ↔ 58개 상세 카테고리(12그룹)* 매핑(평균 ~3 후보 ⇒ 약 155 페어)으로
  Primary + Secondary 후보를 동시에 제시. 각 제안에는 작업 크기를 나타내는
  `effort` (low / medium / high) 가 카탈로그 기본값으로 부여되고, Judge 가
  트레이스 근거로 동적으로 상향(예: high)할 수 있습니다. UI 는 verdict 색
  (pass/warn/fail) 과 충돌하지 않는 별도 팔레트(teal / amber / purple-red)
  로 effort 를 표시하며, low 만 일괄 수락이 허용됩니다. 카탈로그는
  `easyobs.eval.services.improvement_catalog` 가 단일 출처입니다.
  레거시 8키 카테고리(`prompt_clarity` / `retrieval_quality` …)는 Pack 필터
  와 외부 대시보드 호환을 위해 유지되며, 상세 카테고리는 추가 필드로
  덧붙여집니다 (`categoryDetail`, `categoryGroup`, `secondaryCandidates`).
  상태 워크플로는 그대로 (`open → triaged → applied/rejected`).
- **AI Security Hardening 트랙 (Mythos 위협 대응)**: 2026년 4월 Anthropic
  Claude Mythos preview 로 자율형 제로데이 발견·익스플로잇 작성이 현실화된
  이후, 카탈로그를 **safety 11종 + supply 4종** 으로 확장하고 8개 보안
  cause code (`safety.injection_attempt`, `safety.jailbreak_drift`,
  `safety.exfil_url`, `safety.secret_egress`, `safety.self_redact`,
  `supply.third_party_breach`, `supply.sourcemap_leak`,
  `supply.public_cache`) 와 5개 보안 평가자 ID
  (`rule.safety.injection_pattern` 등)를 사전 와이어링했습니다.
  룰 본체 구현은 후속 PR이며, 그 전에도 Mythos급 사고 발생
  시 Improvement Pack 이 대응 카테고리·effort 를 즉시 안내합니다.
- **Auto-rule on ingest**: rule 평가는 트레이스 수집 시점에 fire-and-forget 으로
  실행되고, LLM Judge 는 비용·지연을 고려해 **수동/구간 실행** 으로 분리.

### 3-2. 권한 모델 (조직 × 서비스 2축)

평가 라우터는 기존 `CallerScope` 위에 추가 enum 만으로 통제됩니다:

| Permission | 의미 |
|------------|------|
| `evaluations:read` | Quality 메뉴 진입 / 카탈로그·Run·결과 조회. PO/DV/SA 자동 부여. |
| `evaluations:write` | Profile/Run/Schedule/Improvement 변경. PO·SA 만. DV 는 읽기 전용. |
| `goldensets:write` | Golden Set/Item 작성·삭제·라벨링. PO·SA. |
| `cost_admin` | Cost overview / cost_guard 설정. PO·SA. |

라우터에서는 `_require_org`, `_require_write`, `_project_allowed` 헬퍼로 모든
엔드포인트가 (1) 호출자가 해당 org 멤버인지, (2) 쓰기면 위 권한이 있는지,
(3) `project_id` 가 호출자가 접근 가능한 서비스 범위 안인지 — 3중으로 검증합니다.
프런트 `apps/web/app/workspace/quality/guard.tsx` 의 `canMutateQuality` /
`QualityGuard` 가 동일 정책을 미러링해서 UI 자체가 잠깁니다.

### 3-3. 디렉터리

```
src/easyobs/
├─ eval/
│  ├─ types.py              # 도메인 enum (EvaluatorKind, ConsensusPolicy, ...)
│  ├─ rules/                # Rule 평가자
│  │  ├─ dsl.py             # AST 기반 안전 표현식 DSL
│  │  └─ builtin.py         # 17개 내장 rule
│  ├─ judge/                # LLM-as-a-Judge
│  │  ├─ providers.py       # JudgeProvider 추상화
│  │  ├─ consensus.py       # 합의 정책 4종
│  │  └─ runner.py          # 멀티 judge 실행기
│  ├─ services/             # 비즈니스 서비스 계층
│  │  ├─ profiles.py / runs.py / goldensets.py / judge_models.py
│  │  ├─ schedules.py / improvements.py / cost.py / evaluators.py
│  │  └─ dtos.py
│  └─ auto_rule.py          # ingest 후크 (fire-and-forget)
└─ api/routers/evaluations.py   # /v1/evaluations/* 라우터

apps/web/app/workspace/quality/
├─ page.tsx                 # Overview (KPI · 최근 Run · 비용 구성)
├─ profiles/                # Evaluation Profile CRUD
├─ runs/                    # Run 실행 + 결과 (CSV/JSON 다운로드)
├─ golden/                  # Golden Set / Item (3진입점)
├─ judges/                  # LLM Judge Model 카탈로그
├─ cost/                    # 일/월 비용 대시보드
├─ improvements/            # Improvement Pack 워크플로
└─ guard.tsx                # 권한 가드 + ScopeBanner / WriteHint
```

### 3-4. 검증

- 백엔드: `pytest -q` → **50 passed**
  (rule DSL 안전성 / consensus / cost guard / profile-run 통합 / HTTP 권한)
- 프런트: `npm run build` → **22/22** 페이지 정적 생성 OK
- 운영 회귀: 평가 모듈을 끄면(`EASYOBS_EVAL_ENABLED=false`) 라우터가 등록조차
  되지 않고 기존 ingest/observe/SDK API 만 노출됨 — 동일 테스트 스위트로 보장.

---

## 4. 수집 SDK 빌드 및 배포 (easyobs_agent)

서비스 에이전트에서 EasyObs로 트레이스를 전송하려면 `easyobs_agent` SDK만
설치하면 됩니다. EasyObs 서버 전체를 배포할 필요 없이 **경량 클라이언트 패키지
(~15KB)** 하나와 OpenTelemetry 의존성만으로 동작합니다.

### 4-1. 온라인 환경 (pip 설치)

```bash
pip install -e ".[agent]"
```

### 4-2. 오프라인/폐쇄망 환경 (wheel 빌드)

PyPI 접속이 불가능한 폐쇄망에서는 wheel을 미리 빌드하여 반입합니다.

```powershell
# EasyObs 프로젝트 루트에서 (인터넷 가능 환경)

# easyobs_agent SDK만 wheel 빌드 + 의존성 다운로드
.\scripts\build-agent-sdk.ps1 -IncludeDeps
```

결과물:
```
dist/agent/
├─ easyobs_agent-0.1.0-py3-none-any.whl   ← 수집 SDK (~15KB)
└─ deps/                                    ← OpenTelemetry 의존성
   ├─ opentelemetry_api-*.whl
   ├─ opentelemetry_sdk-*.whl
   ├─ opentelemetry_exporter_otlp_proto_http-*.whl
   └─ ... (transitive dependencies)
```

### 4-3. 대상 서버에 설치

```powershell
# USB 등으로 dist/agent/ 폴더를 반입한 뒤
pip install --no-index --find-links .\deps .\easyobs_agent-0.1.0-py3-none-any.whl
```

```bash
# Linux
pip install --no-index --find-links ./deps ./easyobs_agent-0.1.0-py3-none-any.whl
```

### 4-4. 서비스 코드에서 사용

```python
from easyobs_agent import init, traced

# 초기화 (앱 시작 시 한 번)
init(
    "http://<easyobs-server>:8787",
    token="eobs_...",       # Setup > Organizations > Services 에서 발급
    service="my-service",   # service.name 리소스 속성
)

# 함수 단위 관측
@traced("my.operation")
def do_something(query: str) -> str:
    ...
```

> `@traced` 데코레이터가 함수 호출마다 자동으로 OpenTelemetry Span을 생성하고,
> `init()`에서 설정한 OTLP/HTTP exporter를 통해 EasyObs 서버로 전송합니다.

---

## 5. 라이선스 (License)

EasyObs 는 **PolyForm Noncommercial License 1.0.0** 하에 배포되는
**source-available** 소프트웨어입니다. 전체 조항은 저장소 루트의
[`LICENSE`](./LICENSE) 파일을 참조하세요.

요지는 다음과 같습니다.

| 구분 | 허용 여부 |
| --- | --- |
| 개인 학습·연구·실험·취미 프로젝트 | 허용 |
| 비영리·교육·정부 기관의 비상업적 업무 | 허용 |
| 학술·기술 블로그에서의 인용·시연 | 허용 |
| 비상업적 포크·기여(Pull Request) | 허용 |
| **상업적 서비스/제품에서의 사용·배포** | **금지** |
| **상업적 목적의 파생물(2차 저작물) 개발** | **금지** |
| **사내 운영 도구로의 상시 운영** | **금지** |
| 라이선스 고지·저작권 표시 제거·은닉 | 금지 |

> ⚠️ 본 라이선스는 OSI 의 "Open Source" 정의에는 부합하지 **않습니다**.
> 소스 코드는 공개되어 있으나, 위 표의 "금지" 항목을 위반하는 즉시 라이선스가
> 자동 종료되며, 권리자는 저작권법에 따른 모든 법적 구제 수단을 행사할 수 있습니다.

상업적 사용·재배포·상용 제품 통합 등이 필요하다면, 별도의
**유상 상업적 라이선스(Commercial License)** 계약이 필요합니다.

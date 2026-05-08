# syntax=docker/dockerfile:1.7
# =============================================================================
# EasyObs API (FastAPI + uvicorn) — production multi-stage image.
#
# Build context: 프로젝트 루트 디렉터리
#
#   docker build \
#     -f setup/images/api/Dockerfile \
#     -t easyobs/api:0.2.0 \
#     .
# =============================================================================

# ---- Stage 1: builder -------------------------------------------------------
FROM python:3.12-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# psycopg/asyncpg 용 빌드 의존성. Postgres 드라이버는 wheel 가능 시 그대로 사용.
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential libpq-dev curl ca-certificates \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /build

# 의존성 캐시를 위해 메타데이터만 먼저 복사
COPY pyproject.toml README.md LICENSE ./
COPY src/ ./src/

# venv 에 패키지 + Postgres 드라이버 설치
RUN python -m venv /opt/venv \
 && /opt/venv/bin/pip install --upgrade pip wheel \
 && /opt/venv/bin/pip install ".[agent,pricing,analytics]" "asyncpg>=0.30" "psycopg2-binary>=2.9"

# ---- Stage 2: runtime -------------------------------------------------------
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    EASYOBS_API_HOST=0.0.0.0 \
    EASYOBS_API_PORT=8787 \
    EASYOBS_DATA_DIR=/var/lib/easyobs \
    EASYOBS_LOG_FORMAT=json

# curl: healthcheck 용. libpq5: psycopg2-binary 런타임 의존
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl libpq5 ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && groupadd --system --gid 1100 easyobs \
 && useradd  --system --uid 1100 --gid 1100 --home /var/lib/easyobs --shell /usr/sbin/nologin easyobs \
 && mkdir -p /var/lib/easyobs \
 && chown -R easyobs:easyobs /var/lib/easyobs

COPY --from=builder /opt/venv /opt/venv

USER easyobs
WORKDIR /var/lib/easyobs

EXPOSE 8787

HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=10 \
  CMD curl -fsS "http://127.0.0.1:${EASYOBS_API_PORT}/healthz" || exit 1

# uvicorn workers 수는 EASYOBS_UVICORN_WORKERS 로 조절 (기본 1: 단일 노드).
# alarm evaluator 가 여러 워커에서 동시에 돌면 알람이 중복 발송되므로,
# 운영에서는 1 worker + 컨테이너 N대를 권장 (동일 ALARM_ENABLED=true 는 1대만).
ENV EASYOBS_UVICORN_WORKERS=1

CMD ["sh", "-c", "exec uvicorn easyobs.http_app:create_app --factory --host ${EASYOBS_API_HOST} --port ${EASYOBS_API_PORT} --workers ${EASYOBS_UVICORN_WORKERS} --proxy-headers --forwarded-allow-ips '*'"]

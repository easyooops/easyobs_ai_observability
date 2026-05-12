# EasyObs — Docker Compose (운영 스타일)

**영문 원문:** [`README.md`](README.md)

| 파일 | 역할 |
|------|------|
| `docker-compose.deps.yml` | Postgres |
| `docker-compose.app.yml` | 단일 노드: API + Web |
| `docker-compose.cluster.yml` | 단일 호스트: API 리더 + N 워커 + Web + nginx |
| `nginx.cluster.conf` | `/v1`, `/otlp`, `/healthz`, `/docs` → API; 나머지 → Web |
| `env.sample` | `.env` 템플릿 |

이미지는 사전 빌드(Compose는 태그만 참조; 이 파일들에는 `build:` 없음):

```bash
docker build \
  -f setup/images/api/Dockerfile \
  -t easyobs/api:0.2.0 \
  .

docker build \
  -f setup/images/web/Dockerfile \
  -t easyobs/web:0.2.0 \
  --build-arg NEXT_PUBLIC_API_URL=http://127.0.0.1:8787 \
  apps/web
```

컨텍스트: API = 저장소 루트, Web = `apps/web`.

## 1. 단일 노드

```bash
cd setup/compose
cp env.sample .env
# POSTGRES_PASSWORD, EASYOBS_JWT_SECRET, NEXT_PUBLIC_API_URL 설정 (JWT는 openssl rand -hex 32 등)

docker compose \
  -f docker-compose.deps.yml \
  -f docker-compose.app.yml \
  --env-file .env up -d
```

| 서비스 | URL |
|--------|-----|
| API | `http://<host>:8787` |
| OpenAPI | `http://<host>:8787/docs` |
| Web | `http://<host>:3000` |

초기화(볼륨 삭제):

```bash
docker compose -f docker-compose.deps.yml -f docker-compose.app.yml down -v
```

## 2. 단일 호스트 클러스터

한 VM에서 API 컨테이너 N개 + nginx.

```bash
cd setup/compose
cp env.sample .env  # 필요 시
# .env의 EASYOBS_API_REPLICAS

docker compose \
  -f docker-compose.deps.yml \
  -f docker-compose.cluster.yml \
  --env-file .env up -d \
  --scale easyobs-api-worker=${EASYOBS_API_REPLICAS:-2}
```

진입점: `http://<host>:80` (또는 `EASYOBS_LB_HTTP_PORT`).

**클러스터 유의사항**

1. **알람:** `easyobs-api-leader`만 `EASYOBS_ALARM_ENABLED=true`; 워커는 compose에서 `false`로 고정.
2. **JWT:** 모든 복제본에 동일한 `EASYOBS_JWT_SECRET`; 멀티 호스트는 명시적으로 설정.
3. **Blob:** `easyobs_blob` 볼륨은 단일 호스트 전용; 멀티 호스트는 NFS/EFS 필요(Terraform 클러스터는 EFS 처리).
4. **DB:** Postgres(`EASYOBS_DATABASE_URL`) 사용; 다중 writer에 SQLite 사용 금지.

## 3. 외부 Postgres

`docker-compose.deps.yml`을 빼고 `EASYOBS_DATABASE_URL`을 RDS(또는 기타)로 설정.

```bash
docker compose -f docker-compose.app.yml --env-file .env up -d
# 클러스터:
docker compose -f docker-compose.cluster.yml --env-file .env up -d \
  --scale easyobs-api-worker=4
```

## 4. DuckDB + Parquet (v0.2+)

기본값이 `parquet` + `duckdb`이므로 신규 배포 시 별도 설정이 필요 없습니다.
레거시 NDJSON 모드로 전환하려면 `.env`에서:

```bash
EASYOBS_STORAGE_FORMAT=ndjson
EASYOBS_QUERY_ENGINE=legacy
```

**클라우드 Blob 스토리지 (S3 / Azure / GCS):**

`.env`에서 주석 해제 후 값을 입력하거나, UI **Settings > Storage**에서 설정합니다.
관련 환경 변수는 `env.sample`의 blob 스토리지 블록(S3, Azure, GCS 주석)을 참고하세요.

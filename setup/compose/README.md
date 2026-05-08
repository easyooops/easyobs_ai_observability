# EasyObs — Docker Compose (production-style)

| File | Role |
|------|------|
| `docker-compose.deps.yml` | Postgres |
| `docker-compose.app.yml` | Single node: API + Web |
| `docker-compose.cluster.yml` | Single host: API leader + N workers + Web + nginx |
| `nginx.cluster.conf` | Routes `/v1`, `/otlp`, `/healthz`, `/docs` → API; rest → Web |
| `env.sample` | `.env` template |

Pre-build images (Compose references tags; no `build:` in these files):

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

Contexts: API = repo root, Web = `apps/web`.

## 1. Single node

```bash
cd setup/compose
cp env.sample .env
# Set POSTGRES_PASSWORD, EASYOBS_JWT_SECRET, NEXT_PUBLIC_API_URL (openssl rand -hex 32 for JWT)

docker compose \
  -f docker-compose.deps.yml \
  -f docker-compose.app.yml \
  --env-file .env up -d
```

| Service | URL |
|---------|-----|
| API | `http://<host>:8787` |
| OpenAPI | `http://<host>:8787/docs` |
| Web | `http://<host>:3000` |

Reset (wipe volumes):

```bash
docker compose -f docker-compose.deps.yml -f docker-compose.app.yml down -v
```

## 2. Single-host cluster

N API containers + nginx on one VM.

```bash
cd setup/compose
cp env.sample .env  # if needed
# EASYOBS_API_REPLICAS in .env

docker compose \
  -f docker-compose.deps.yml \
  -f docker-compose.cluster.yml \
  --env-file .env up -d \
  --scale easyobs-api-worker=${EASYOBS_API_REPLICAS:-2}
```

Entry: `http://<host>:80` (or `EASYOBS_LB_HTTP_PORT`).

**Cluster notes**

1. **Alarms:** only `easyobs-api-leader` has `EASYOBS_ALARM_ENABLED=true`; workers forced `false` in compose.
2. **JWT:** same `EASYOBS_JWT_SECRET` on all replicas; for multi-host, set explicitly.
3. **Blob:** `easyobs_blob` volume is single-host only; multi-host needs NFS/EFS (Terraform cluster handles EFS).
4. **DB:** use Postgres (`EASYOBS_DATABASE_URL`); not SQLite for multiple writers.

## 3. External Postgres

Omit `docker-compose.deps.yml`; set `EASYOBS_DATABASE_URL` to RDS (or other).

```bash
docker compose -f docker-compose.app.yml --env-file .env up -d
# cluster:
docker compose -f docker-compose.cluster.yml --env-file .env up -d \
  --scale easyobs-api-worker=4
```

## 4. DuckDB + Parquet (v0.2+)

기본값이 `parquet` + `duckdb` 이므로 신규 배포 시 별도 설정 불필요.
레거시 NDJSON 모드로 전환하려면 `.env`에서:

```bash
EASYOBS_STORAGE_FORMAT=ndjson
EASYOBS_QUERY_ENGINE=legacy
```

**클라우드 Blob 스토리지 (S3 / Azure / GCS):**

`.env`에서 주석 해제 후 값 입력, 또는 UI Settings > Storage 에서 설정.
자세한 환경 변수는 `env.sample`의 `클라우드 Blob 스토리지` 섹션 참조.

# EasyObs

Lightweight AI observability: **OpenAPI**, **SQLite or Postgres**, **object-first blob** (local / S3·Azure·GCS), **OTLP/HTTP** ingest.

---

## 1. Run locally

### Requirements

- Python **3.11+**
- Node.js **20+** and npm
- Optional: [uv](https://docs.astral.sh/uv/) for venvs

Commands assume **repository root**.

### Windows (PowerShell)

```powershell
Copy-Item .env.sample .env
.\scripts\run-dev.ps1
```

Options:

```powershell
.\scripts\run-dev.ps1 -ApiPort 8787 -WebPort 3000
.\scripts\run-dev.ps1 -SkipInstall
.\scripts\run-dev.ps1 -LogFormat json -LogLevel DEBUG
```

Starts API in the background, Next.js in the foreground; **Ctrl+C** stops both. API logs: `data/api.log` + terminal.

Reset local data:

```powershell
.\.venv\Scripts\easyobs.exe reset-data --force --yes
.\scripts\run-dev.ps1
```

### Linux / macOS

```bash
chmod +x scripts/run-dev.sh
cp .env.sample .env
./scripts/run-dev.sh
```

Options:

```bash
API_PORT=8787 WEB_PORT=3000 ./scripts/run-dev.sh
./scripts/run-dev.sh --skip-install
EASYOBS_LOG_FORMAT=json EASYOBS_LOG_LEVEL=DEBUG ./scripts/run-dev.sh
```

Reset:

```bash
./.venv/bin/easyobs reset-data --force --yes
./scripts/run-dev.sh
```

### URLs

| Service | URL |
|---------|-----|
| Web | http://localhost:3000 |
| API | http://127.0.0.1:8787 |
| OpenAPI | http://127.0.0.1:8787/docs |
| Health | http://127.0.0.1:8787/healthz |

First signup → super admin on org `administrator`. With `EASYOBS_SEED_MOCK_DATA=true`, first boot seeds demo traces (skipped if any traces already exist).

---

## 2. Environment variables

Defaults: `src/easyobs/settings.py`. Use `.env` or real env in containers. Template: [`.env.sample`](.env.sample).

### Storage

| Variable | Default | Notes |
|----------|---------|-------|
| `EASYOBS_DATA_DIR` | `./data` | SQLite catalog, JWT file, trace blobs; `easyobs reset-data` clears this |
| `EASYOBS_DATABASE_URL` | (derived) | Empty → `<DATA_DIR>/catalog.sqlite3`. Prod: `postgresql+asyncpg://...` |

### HTTP

| Variable | Default | Notes |
|----------|---------|-------|
| `EASYOBS_API_HOST` | `127.0.0.1` | Use `0.0.0.0` in containers |
| `EASYOBS_API_PORT` | `8787` | API + OTLP |
| `EASYOBS_CORS_ORIGINS` | localhost dev origins | Comma-separated; add non-local origins explicitly |

### Auth

| Variable | Default | Notes |
|----------|---------|-------|
| `EASYOBS_JWT_SECRET` | auto | HS256; empty → created under `<DATA_DIR>/jwt.secret` |
| `EASYOBS_JWT_TTL_HOURS` | `12` | |

### LLM pricing (ingest)

| Variable | Default | Notes |
|----------|---------|-------|
| `EASYOBS_PRICING_SOURCE` | `auto` | `tokencost` → `litellm` → builtin; or force one of those |

### Logging

| Variable | Default | Notes |
|----------|---------|-------|
| `EASYOBS_LOG_LEVEL` | `INFO` | |
| `EASYOBS_LOG_FORMAT` | `console` | `json` for log stacks |
| `EASYOBS_LOG_FILE` | (none) | Mirror to file |
| `EASYOBS_LOG_REQUEST_BODY` | `false` | `true` logs JSON bodies (PII risk) |

### Demo seed (first boot only)

| Variable | Default | Notes |
|----------|---------|-------|
| `EASYOBS_SEED_MOCK_DATA` | `false` | Skipped if catalog already has traces |
| `EASYOBS_SEED_MOCK_TRACES` | `100` | |
| `EASYOBS_SEED_MOCK_WINDOW_HOURS` | `24` | |

### Frontend (`apps/web`)

| Variable | Default | Notes |
|----------|---------|-------|
| `NEXT_PUBLIC_API_URL` | `http://127.0.0.1:8787` | Browser-visible API base |

### Quality / eval feature flags

| Variable | Default | Notes |
|----------|---------|-------|
| `EASYOBS_EVAL_ENABLED` | `true` | `false` disables `/v1/evaluations/*` and Quality UI |
| `EASYOBS_EVAL_AUTO_RULE_ON_INGEST` | `true` | Fire-and-forget rule eval after ingest; failures don’t fail ingest |

---

## 3. Quality (eval) module — overview

- **Profiles:** Combine rule-based eval, multi-LLM judges (consensus policies), and human/golden labels in one run.
- **Cost guard:** Per-run / per-subject / monthly budgets on judge spend.
- **Golden sets:** Multiple entry paths (manual, generated candidates, trace labeling).
- **Improvement Pack:** Maps low scores to catalogued metrics/categories with `effort` hints; catalog source: `easyobs.eval.services.improvement_catalog`.
- **Security-oriented catalog extensions:** Extra safety/supply dimensions and cause codes wired for future rules; see `src/easyobs/eval/` for implementation.
- **Auto-rule vs judges:** Rules can run on ingest; LLM judges are typically on-demand for cost/latency.

### Permissions (org × service)

| Permission | Scope |
|------------|--------|
| `evaluations:read` | View Quality / runs / catalog |
| `evaluations:write` | Mutate profiles, runs, schedules, improvements |
| `goldensets:write` | Golden sets and labels |
| `cost_admin` | Cost overview and `cost_guard` |

### Layout

```
src/easyobs/eval/          # types, rules, judge, services, auto_rule hook
src/easyobs/api/routers/evaluations.py
apps/web/app/workspace/quality/   # UI
```

### Checks (maintainer notes)

- Backend: `pytest -q` → **50 passed** (rules, consensus, cost guard, HTTP auth).
- Frontend: `npm run build` in `apps/web` → **22/22** static pages OK.
- With `EASYOBS_EVAL_ENABLED=false`, eval routes are not registered.

---

## 4. `easyobs_agent` SDK

Lightweight client (~15KB) + OpenTelemetry deps; sends traces to EasyObs via OTLP/HTTP.

### Online

```bash
pip install -e ".[agent]"
```

### Offline wheels

From repo root (online build machine):

```powershell
.\scripts\build-agent-sdk.ps1 -IncludeDeps
```

Output:

```
dist/agent/
├─ easyobs_agent-0.1.0-py3-none-any.whl
└─ deps/   # OpenTelemetry wheels
```

### Install on target

```powershell
pip install --no-index --find-links .\deps .\easyobs_agent-0.1.0-py3-none-any.whl
```

```bash
pip install --no-index --find-links ./deps ./easyobs_agent-0.1.0-py3-none-any.whl
```

### Usage

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

`@traced` creates spans; `init()` configures the OTLP/HTTP exporter.

---

## 5. License

EasyObs is **source-available** under the **PolyForm Noncommercial License 1.0.0**. Full text: [`LICENSE`](./LICENSE).

Summary:

| Use case | Allowed |
|----------|---------|
| Personal learning, research, hobby | Yes |
| Nonprofit / education / noncommercial government use | Yes |
| Citing or demoing in noncommercial blogs | Yes |
| Noncommercial forks / PRs | Yes |
| Commercial product or service | **No** |
| Commercial derivatives | **No** |
| Always-on internal commercial operations | **No** |
| Removing license / copyright notices | **No** |

Not OSI “Open Source.” Commercial use needs a separate commercial license from the rights holder.

---

Production deploy: see [`setup/README.md`](setup/README.md) (Terraform, Compose, air-gapped).

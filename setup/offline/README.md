# EasyObs — air-gapped (offline) deployment

**Build machine (online):** produce a bundle. **Target host(s) (offline):** load images and run scripts.

Bundle: Docker image tars + `easyobs-source.tar.gz` + `easyobs-product.tar.gz` + deploy scripts. Works without a private registry.

---

## Bundle contents

| Artifact | Source | Role |
|----------|--------|------|
| `easyobs-images.tar` | `docker save` easyobs/api, easyobs/web | App images |
| `third-party-images.tar` | `docker save` postgres, nginx, … | Dependencies |
| `easyobs-source.tar.gz` | Repo | Debug / rebuild |
| `easyobs-product.tar.gz` | `setup/` | Compose, Dockerfiles, scripts |
| `load-bundle.sh` | Bundled | `docker load` + extract |
| `deploy-single.sh` | Bundled | Single node |
| `deploy-cluster.sh` | Bundled | single-host / multi-host |
| `manifest.txt` | Generated | File list + sha256 |

---

## 1. Build machine

**Requires:** Docker (24+), repo checkout; `tar` available (Linux/macOS/WSL; PowerShell script also provided).

**Linux/macOS/WSL:**

```bash
cd <repo-root>
./setup/offline/build-bundle.sh \
    --output ./dist/easyobs-bundle \
    --api-tag easyobs/api:0.2.0 \
    --web-tag easyobs/web:0.2.0
```

**Windows PowerShell:**

```powershell
cd <repo-root>
.\setup\offline\build-bundle.ps1 `
    -Output .\dist\easyobs-bundle `
    -ApiTag easyobs/api:0.2.0 `
    -WebTag easyobs/web:0.2.0
```

Copy `./dist/easyobs-bundle/` to the air-gapped network. Verify with `manifest.txt` sha256.

Keep image tags aligned across bundle, `.env`, and Terraform when you version releases.

---

## 2. Target host(s)

**Requires:** Linux host(s), Docker 24+ and Compose v2 (install via your offline package flow if needed), docker group or `sudo docker`.

### 2-1. Load bundle

```bash
cd /path/to/easyobs-bundle
./load-bundle.sh
```

Actions: `docker load` both tars; extract source/product under `/opt/easyobs/{src,product}` (override: `EASYOBS_TARGET_DIR=/data/easyobs ./load-bundle.sh`).

### 2-2-A. Single node

```bash
./deploy-single.sh
```

Creates `.env` from `env.sample` if missing; can auto-fill `EASYOBS_JWT_SECRET` and `POSTGRES_PASSWORD`. Starts deps + app compose.

| Service | URL |
|---------|-----|
| API | `http://<host>:8787` |
| OpenAPI | `http://<host>:8787/docs` |
| Web | `http://<host>:3000` |

Update CORS / `NEXT_PUBLIC_API_URL` in `.env`, then `docker compose ... up -d` again.

### 2-2-B. Single-host cluster

```bash
./deploy-cluster.sh single-host 4
```

Entry: `http://<host>:80` (or `EASYOBS_LB_HTTP_PORT`).

### 2-2-C. Multi-host cluster

Prereqs: shared Postgres, shared blob mount (`EASYOBS_BLOB_HOST_DIR`), LB in front of API pool (and Web).

```bash
export EASYOBS_DATABASE_URL='postgresql+asyncpg://easyobs:****@db.internal:5432/easyobs'
export EASYOBS_JWT_SECRET='<same on all hosts>'
export EASYOBS_BLOB_HOST_DIR='/mnt/nfs/easyobs-blob/data'
export EASYOBS_PUBLIC_BASE_URL='http://easyobs.intra.example.com'

./deploy-cluster.sh multi-host leader   # one host
./deploy-cluster.sh multi-host worker   # N hosts
./deploy-cluster.sh multi-host web
```

Leader only: `EASYOBS_ALARM_ENABLED=true` (script enforces on workers).

`NEXT_PUBLIC_API_URL` is build-time: rebuild web image with correct `--build-arg` on the build machine and re-bundle if the public API URL changes.

---

## 3. Operations

- **Upgrade:** New bundle → `load-bundle.sh` → `docker compose up -d` / redeploy scripts.
- **Migrations:** Schema via SQLAlchemy `create_all` on startup; no Alembic in tree — plan major upgrades with backup/restore.
- **Backup:** `pg_dump` + blob (NFS/volume snapshot).
- **Logs:** `docker logs` or `EASYOBS_LOG_FILE` + bind mount.
- **Private registry:** Optional — `docker tag`/`push` after load, then point deploy tags at registry.

---

## 4. Troubleshooting

| Symptom | Check |
|---------|--------|
| `docker load` permission | `sudo` / docker group |
| Leader not healthy | DB reachable (SG/route); `docker logs` first minute |
| Duplicate alarms | Workers must not have `EASYOBS_ALARM_ENABLED=true` |
| Web cannot reach API | Browser-reachable `NEXT_PUBLIC_API_URL` |
| sha256 mismatch | Corrupt transfer; re-copy using `manifest.txt` |

# EasyObs — production deployment

Deploy paths share the **same container images** (`easyobs/api`, `easyobs/web`):

- **Online:** AWS Terraform
- **Single host:** Docker Compose
- **Air-gapped:** image tar bundle + scripts

```
setup/
├── images/
│   ├── api/Dockerfile
│   └── web/Dockerfile
├── compose/
│   ├── docker-compose.deps.yml
│   ├── docker-compose.app.yml
│   ├── docker-compose.cluster.yml
│   ├── nginx.cluster.conf
│   ├── env.sample
│   └── README.md
├── ec2/terraform/
│   ├── single/
│   └── cluster/
└── offline/
    ├── build-bundle.sh / .ps1
    ├── load-bundle.sh
    ├── deploy-single.sh
    ├── deploy-cluster.sh
    └── README.md
```

---

## Quick routing

| Scenario | Start here |
|----------|------------|
| AWS, one EC2 | [`terraform-easyobs-ec2.md`](./terraform-easyobs-ec2.md) §1 |
| AWS, scaled API | [`terraform-easyobs-ec2.md`](./terraform-easyobs-ec2.md) §2 |
| On-prem VM, single | [`compose/README.md`](./compose/README.md) §1 |
| On-prem VM, N API replicas | [`compose/README.md`](./compose/README.md) §2 |
| Air-gapped single | [`offline/README.md`](./offline/README.md) §2-2-A |
| Air-gapped single-host cluster | [`offline/README.md`](./offline/README.md) §2-2-B |
| Air-gapped multi-host | [`offline/README.md`](./offline/README.md) §2-2-C |

Build context = **source tree** (Dockerfile paths under `setup/images/`; `COPY` is relative to the context you pass to `docker build`).

---

## Build images (all paths)

From repo root:

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

`NEXT_PUBLIC_API_URL` is a **build-time** Next.js variable; change and rebuild for production domains. Terraform `cluster/` can inject ALB DNS in user_data.

---

## Scaling levels

| Level | Status |
|-------|--------|
| Single process | Default |
| Same host, N API containers | `compose/docker-compose.cluster.yml` + nginx |
| Multi-host + managed DB + shared blob | Terraform `cluster/` or offline multi-host |
| Queue-backed workers | **Not in OSS** (would need new code) |

Rules for multi-instance:

- **Alarms:** exactly one container with `EASYOBS_ALARM_ENABLED=true`.
- **JWT:** same `EASYOBS_JWT_SECRET` everywhere (or shared volume for auto-generated secret).
- **DB:** shared Postgres; do not use SQLite across writers.
- **Blob:** named volume on one host; NFS/EFS (or similar) across hosts.

---

## Security / ops checklist

- [ ] Strong `EASYOBS_JWT_SECRET` (Terraform can generate; verify for manual `.env`).
- [ ] Backup `POSTGRES_PASSWORD` / RDS password.
- [ ] Keep `EASYOBS_LOG_REQUEST_BODY=false` in prod.
- [ ] `EASYOBS_SEED_MOCK_DATA=false` in prod.
- [ ] HTTPS: default is HTTP; use ALB + ACM (or equivalent) in prod.
- [ ] Set `EASYOBS_CORS_ORIGINS` to real console origins.
- [ ] Backup Postgres + blob storage on a schedule.

---

## Guides

| Topic | Location |
|-------|----------|
| AWS EC2 (Terraform) | [`terraform-easyobs-ec2.md`](./terraform-easyobs-ec2.md) |
| Compose | [`compose/README.md`](./compose/README.md) |
| Air-gapped | [`offline/README.md`](./offline/README.md) |
| Dev / local | [`../README.md`](../README.md) |

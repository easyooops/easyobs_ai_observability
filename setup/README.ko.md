# EasyObs — 운영 배포

**영문 원문:** [`setup/README.md`](README.md)

배포 경로는 모두 **동일한 컨테이너 이미지**(`easyobs/api`, `easyobs/web`)를 공유합니다.

- **온라인:** AWS Terraform
- **단일 호스트:** Docker Compose
- **폐쇄망:** 이미지 tar 번들 + 스크립트

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

## 빠른 라우팅

| 시나리오 | 여기서 시작 |
|----------|-------------|
| AWS, EC2 1대 | [`terraform-easyobs-ec2.ko.md`](./terraform-easyobs-ec2.ko.md) §1 |
| AWS, API 스케일 아웃 | [`terraform-easyobs-ec2.ko.md`](./terraform-easyobs-ec2.ko.md) §2 |
| 온프레미스 VM, 단일 | [`compose/README.ko.md`](./compose/README.ko.md) §1 |
| 온프레미스 VM, API N개 복제 | [`compose/README.ko.md`](./compose/README.ko.md) §2 |
| 폐쇄망 단일 | [`offline/README.ko.md`](./offline/README.ko.md) §2-2-A |
| 폐쇄망 단일 호스트 클러스터 | [`offline/README.ko.md`](./offline/README.ko.md) §2-2-B |
| 폐쇄망 멀티 호스트 | [`offline/README.ko.md`](./offline/README.ko.md) §2-2-C |

빌드 컨텍스트 = **소스 트리**(`setup/images/` 아래 Dockerfile 경로; `COPY`는 `docker build`에 넘기는 컨텍스트 기준).

---

## 이미지 빌드(모든 경로)

저장소 루트에서:

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

`NEXT_PUBLIC_API_URL`은 **빌드 타임** Next.js 변수입니다. 운영 도메인에 맞게 바꾼 뒤 재빌드하세요. Terraform `cluster/`는 user_data에서 ALB DNS를 주입할 수 있습니다.

---

## 스케일 단계

| 단계 | 상태 |
|------|------|
| 단일 프로세스 | 기본 |
| 동일 호스트, API 컨테이너 N개 | `compose/docker-compose.cluster.yml` + nginx |
| 멀티 호스트 + 관리형 DB + 공유 blob | Terraform `cluster/` 또는 오프라인 멀티 호스트 |
| 큐 기반 워커 | **OSS에 없음**(새 코드 필요) |

다중 인스턴스 규칙:

- **알람:** `EASYOBS_ALARM_ENABLED=true`인 컨테이너는 정확히 하나.
- **JWT:** 모든 곳에서 동일한 `EASYOBS_JWT_SECRET`(또는 자동 생성 시크릿용 공유 볼륨).
- **DB:** 공유 Postgres; writer 간 SQLite 사용 금지.
- **Blob:** 한 호스트에서는 named volume; 호스트 간에는 NFS/EFS(또는 유사).

---

## 보안 / 운영 체크리스트

- [ ] 강한 `EASYOBS_JWT_SECRET`(Terraform 생성 가능; 수동 `.env`는 검증).
- [ ] `POSTGRES_PASSWORD` / RDS 비밀번호 백업.
- [ ] 운영에서 `EASYOBS_LOG_REQUEST_BODY=false` 유지.
- [ ] 운영에서 `EASYOBS_SEED_MOCK_DATA=false`.
- [ ] `EASYOBS_STORAGE_FORMAT=parquet` + `EASYOBS_QUERY_ENGINE=duckdb` (v0.2+ 기본값, 권장).
- [ ] S3/Azure/GCS blob 사용 시 `setup/compose/env.sample`의 Cloud Blob 섹션 참고.
- [ ] HTTPS: 기본은 HTTP; 운영에서는 ALB + ACM(또는 동등) 사용.
- [ ] `EASYOBS_CORS_ORIGINS`를 실제 콘솔 오리진으로 설정.
- [ ] Postgres + blob 스토리지를 주기적으로 백업.

---

## 가이드

| 주제 | 위치 |
|------|------|
| AWS EC2 (Terraform) | [`terraform-easyobs-ec2.ko.md`](./terraform-easyobs-ec2.ko.md) |
| Compose | [`compose/README.ko.md`](./compose/README.ko.md) |
| 폐쇄망 | [`offline/README.ko.md`](./offline/README.ko.md) |
| 개발 / 로컬 | [`../README.ko.md`](../README.ko.md) |

# EasyObs — 운영 배포 패키지

개발 코드를 운영 환경에 한 번에 배포하기 위한 산출물 묶음.
**온라인(AWS Terraform)**, **단일 호스트 docker compose**,
**폐쇄망(이미지 tar 번들)** 세 가지 경로 모두를 동일한
컨테이너 이미지(=같은 코드)로 커버한다.

```
setup/
├── images/
│   ├── api/Dockerfile               FastAPI + uvicorn (multi-stage)
│   └── web/Dockerfile               Next.js 15 standalone
├── compose/
│   ├── docker-compose.deps.yml      Postgres
│   ├── docker-compose.app.yml       단일 노드: API + Web 1대씩
│   ├── docker-compose.cluster.yml   단일 호스트 클러스터: leader + worker N + Web + nginx
│   ├── nginx.cluster.conf           클러스터용 LB 설정
│   ├── env.sample                   .env 템플릿
│   └── README.md                    compose 사용 가이드
├── ec2/terraform/
│   ├── single/                      AWS 단일 EC2 (compose 풀스택)
│   └── cluster/                     AWS ALB + ASG(API worker) + leader + RDS + EFS
└── offline/
    ├── build-bundle.sh / .ps1       빌드 머신: docker save tar 번들 생성
    ├── load-bundle.sh               타깃 호스트: docker load + tar 풀기
    ├── deploy-single.sh             타깃 호스트: 단일 노드 자동 기동
    ├── deploy-cluster.sh            타깃 호스트: 클러스터 자동 기동
    └── README.md                    폐쇄망 절차서
```

---

## 1. 빠른 결정 표

| 환경 | 토폴로지 | 시작점 |
|------|----------|--------|
| AWS, 1대 가볍게 | EC2 단일 | `terraform-easyobs-ec2.md` § 1 |
| AWS, 워커 확장 | EC2 + ALB + ASG | `terraform-easyobs-ec2.md` § 2 |
| 온프렘 VM 1대 | docker compose 단일 | `compose/README.md` § 1 |
| 온프렘 VM 1대 + 워커 N | docker compose 클러스터 | `compose/README.md` § 2 |
| **폐쇄망 단일** | tar 번들 + `deploy-single.sh` | `offline/README.md` § 2-2-A |
| **폐쇄망 단일 호스트 클러스터** | tar 번들 + `deploy-cluster.sh single-host` | `offline/README.md` § 2-2-B |
| **폐쇄망 다중 호스트 클러스터** | tar 번들 + `deploy-cluster.sh multi-host` | `offline/README.md` § 2-2-C |

> 모든 경로는 **같은 두 이미지(`easyobs/api`, `easyobs/web`)** 를 사용한다.
> 빌드는 **빌드 컨텍스트 = 소스 디렉터리** 임에 주의하면 어디서든 동일하게
> 만든다 (Dockerfile 만 product 트리에 있고, COPY 는 컨텍스트 기준).

---

## 2. 이미지 빌드 (모든 경로 공통)

프로젝트 루트에서:

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

`NEXT_PUBLIC_API_URL` 은 **Next.js 빌드 타임 변수** 이므로, 운영에 띄울
도메인이 다르다면 그 도메인을 박아 다시 빌드해야 한다. AWS Terraform
`cluster/` 는 ALB DNS 를 user_data 에서 자동으로 박는다.

---

## 3. 워커 노드 확장 — 현재 상태와 확장 경로

| 레벨 | 상태 | 비고 |
|------|------|------|
| **Level 0** 단일 프로세스 | 기본 동작 | dev / 단일 노드 운영 |
| **Level 1** 단일 호스트 컨테이너 N대 | 본 패키지로 즉시 가능 | `compose/docker-compose.cluster.yml` (nginx 분산) |
| **Level 2** 다중 호스트 + 매니지드 DB + 공유 blob | 본 패키지로 즉시 가능 | AWS `ec2/terraform/cluster/` (ALB+ASG+RDS+EFS) 또는 폐쇄망 multi-host |
| **Level 3** 큐 기반 워커 분리 | **OSS 미구현** (코드 변경 필요) | Redis Streams/Celery 도입 후 별도 평가 워커 분리 |

Level 1·2 에서는 다음 규칙이 자동 강제된다.

- **Alarm leader**: `EASYOBS_ALARM_ENABLED=true` 인 컨테이너는 항상 1개. 워커는
  `false`. 알람 중복 발송 방지.
- **JWT 시크릿 공유**: 모든 컨테이너가 동일 `EASYOBS_JWT_SECRET` 사용. 비워두면
  공유 볼륨/EFS 의 `jwt.secret` 파일이 자동 생성·재사용.
- **DB 공유**: 모든 인스턴스가 같은 Postgres URL. SQLite 는 절대 멀티 라이터로
  쓰지 않음.
- **Blob 공유**: 단일 호스트는 docker named volume, 다중 호스트는 NFS/EFS 마운트.

Level 3 가 필요해지는 시점(예: ingest QPS 가 수만, 평가 비용·지연이 ingest
응답 시간을 끌어올리기 시작) 에는 코드에 큐를 끼우는 별도 PR 이 필요하다.
이 패키지는 그 단계까지 가지 않더라도 부하를 줄일 수 있는 설계 한도까지를
즉시 제공하는 것을 목표로 한다.

---

## 4. 보안·운영 체크리스트

- [ ] `EASYOBS_JWT_SECRET` 을 운영 시작 전에 강한 32 byte hex 로 교체
      (Terraform 은 자동 생성하지만 `.env` 수동 설정 시 잊기 쉬움).
- [ ] `POSTGRES_PASSWORD` (또는 RDS password) 백업.
- [ ] `EASYOBS_LOG_REQUEST_BODY=false` 유지 (PII/시크릿 누출 방지).
- [ ] `EASYOBS_SEED_MOCK_DATA=false` (운영). 데모 트레이스가 운영 DB 에
      들어가지 않도록.
- [ ] HTTPS: 본 패키지는 HTTP 80/8787 직노출 기본. 운영에서는 ALB listener
      를 HTTPS 로 바꾸고 ACM 인증서 부착 권장 (terraform 변수만 추가하면 됨).
- [ ] CORS: `EASYOBS_CORS_ORIGINS` 에 실제 콘솔 도메인 추가.
- [ ] Backup: Postgres `pg_dump` + Blob (named volume / EFS / NFS) 주기 스냅샷.

---

## 5. 관련 가이드

| 가이드 | 위치 |
|--------|------|
| AWS EC2 (Terraform)         | [`./terraform-easyobs-ec2.md`](./terraform-easyobs-ec2.md) |
| docker compose (온/오프 공통)  | [`./compose/README.md`](./compose/README.md) |
| 폐쇄망 절차서                | [`./offline/README.md`](./offline/README.md) |
| 이미지 빌드 컨텍스트 메모    | 본 README § 2 |
| EasyObs 개발 문서             | [`../README.md`](../README.md) |

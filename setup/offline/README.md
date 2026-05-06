# EasyObs — 폐쇄망(오프라인) 배포 가이드

이 디렉터리는 인터넷 접속이 불가능한 환경(폐쇄망/Air-gapped)에 EasyObs 를
배포하기 위한 번들 스크립트와 가이드를 담는다. **반입 전(빌드 머신)** 과
**반입 후(타깃 호스트)** 두 단계로 구성된다.

산출물 형태: **Docker 이미지 tar 번들 + 소스/compose tar.gz + 자동 배포 스크립트**.
사설 레지스트리(Harbor/Nexus) 없이도 동작한다.

---

## 0. 산출물 한눈에 보기

| 파일 | 출처 | 역할 |
|------|------|------|
| `easyobs-images.tar`     | `docker save easyobs/api:* easyobs/web:*` | EasyObs 컨테이너 이미지 |
| `third-party-images.tar` | `docker save postgres:16 nginx:1.27-alpine` | 의존 이미지 |
| `easyobs-source.tar.gz`  | 프로젝트 소스     | 소스 (재빌드/디버그용) |
| `easyobs-product.tar.gz` | `setup/`          | compose, Dockerfile, terraform, offline 스크립트 |
| `load-bundle.sh`         | 동봉                                        | 타깃에서 docker load + tar 풀기 |
| `deploy-single.sh`       | 동봉                                        | 단일 노드 자동 기동 |
| `deploy-cluster.sh`      | 동봉                                        | 클러스터 기동 (single-host / multi-host) |
| `manifest.txt`           | 자동 생성                                   | 산출물 목록 + sha256 |

---

## 1. 빌드 머신 (인터넷 가능)

### 사전 조건

- Docker (24+ 권장)
- 프로젝트 루트 체크아웃
- (선택) Linux/macOS 또는 WSL — Windows PowerShell 도 지원하지만 `tar` 가 필요

### 번들 만들기 (Linux/macOS/WSL)

```bash
cd <repo-root>
./setup/offline/build-bundle.sh \
    --output ./dist/easyobs-bundle \
    --api-tag easyobs/api:0.2.0 \
    --web-tag easyobs/web:0.2.0
```

### 번들 만들기 (Windows PowerShell)

```powershell
cd <repo-root>
.\setup\offline\build-bundle.ps1 `
    -Output .\dist\easyobs-bundle `
    -ApiTag easyobs/api:0.2.0 `
    -WebTag easyobs/web:0.2.0
```

`./dist/easyobs-bundle/` 디렉터리 통째를 USB / 매체 / 사내 전송 경로 등으로
폐쇄망 호스트에 옮기면 된다. 검증은 `manifest.txt` 의 sha256 으로.

> **이미지 태그 고정**: 운영 환경의 `.env` 와 Terraform 변수에 동일한 태그를
> 박아 두면 버전 추적이 쉽다. 번들 새로 만들 때마다 `0.2.1`, `0.2.2` ...
> 처럼 올려서 반입.

---

## 2. 타깃 호스트 (폐쇄망)

### 사전 조건

- Linux (Ubuntu 22.04 / RHEL 9 / Rocky 9 등) 호스트 1대 이상
- Docker (24+) + Docker Compose v2 가 미리 설치되어 있어야 한다.
  (Docker 자체도 폐쇄망이면 별도 RPM/DEB 반입 필요)
- 사용자가 docker 그룹에 속하거나 `sudo docker` 사용 가능

### 2-1. 번들 로딩

번들을 옮긴 디렉터리에서 한 번만 실행:

```bash
cd /path/to/easyobs-bundle
./load-bundle.sh
```

스크립트가 다음을 수행한다.

1. `easyobs-images.tar`, `third-party-images.tar` 를 `docker load`.
2. `easyobs-source.tar.gz`, `easyobs-product.tar.gz` 를 `/opt/easyobs/{src,product}` 로 압축 해제.
3. 로딩된 이미지 목록을 출력.

호스트 외 장소를 쓰고 싶으면 `EASYOBS_TARGET_DIR=/data/easyobs ./load-bundle.sh`.

### 2-2-A. 단일 노드 배포

가장 단순. API + Web + Postgres 한 호스트에 함께 띄움.

```bash
./deploy-single.sh
```

스크립트가 .env 가 없으면 `env.sample` 에서 복사하고 `EASYOBS_JWT_SECRET`,
`POSTGRES_PASSWORD` 를 자동 생성해 끼워 넣는다. 그 다음
`docker-compose.deps.yml` + `docker-compose.app.yml` 로 기동.

| 서비스 | URL |
|--------|-----|
| API     | `http://<host>:8787` |
| OpenAPI | `http://<host>:8787/docs` |
| Web 콘솔 | `http://<host>:3000` |

> CORS / `NEXT_PUBLIC_API_URL` 을 외부 호스트명에 맞춰 바꾸려면 `.env` 의
> 해당 변수를 수정한 뒤 다시 `docker compose ... up -d` 로 적용.

### 2-2-B. 단일 호스트 클러스터 (Worker 수평 확장)

한 호스트 안에서 API 컨테이너 N대를 띄우고 nginx 가 분산. 코드 변경 없는
가장 빠른 워커 확장 경로.

```bash
./deploy-cluster.sh single-host 4   # API worker 4대로 시작
```

진입점은 `http://<host>:80` (.env 의 `EASYOBS_LB_HTTP_PORT` 로 변경).

### 2-2-C. 다중 호스트 클러스터 (Worker 노드를 별도 VM 으로)

호스트 여러 대에 역할(role)을 분배해 컨테이너 1개씩만 띄움. 진정한 워커
확장 효과를 얻으려면 다음 외부 인프라가 미리 준비돼야 한다.

| 자원 | 책임 |
|------|------|
| Postgres (외부 매니지드/온프렘 클러스터) | 모든 API 호스트가 같은 DB 를 본다 |
| NFS 공유 (또는 EFS, 별도 파일러) | 모든 API 호스트가 `EASYOBS_BLOB_HOST_DIR` 로 마운트 |
| LB (HAProxy / nginx / VIP) | API 호스트 풀 앞단에서 라운드로빈 분산 |
| LB | Web 호스트 1대 앞단(또는 같은 LB 의 다른 path) |

각 호스트에서:

```bash
# 모든 호스트 공통
export EASYOBS_DATABASE_URL='postgresql+asyncpg://easyobs:****@db.internal:5432/easyobs'
export EASYOBS_JWT_SECRET='<32-byte hex, 모든 호스트 동일>'
export EASYOBS_BLOB_HOST_DIR='/mnt/nfs/easyobs-blob/data'
export EASYOBS_PUBLIC_BASE_URL='http://easyobs.intra.example.com'

# leader 호스트 (1대만)
./deploy-cluster.sh multi-host leader

# worker 호스트 (N대)
./deploy-cluster.sh multi-host worker

# web 호스트 (1대 — 또는 SSR 분산을 위해 N대)
./deploy-cluster.sh multi-host web
```

> **Alarm 중복 방지**: leader 1대만 `EASYOBS_ALARM_ENABLED=true`. 워커는
> 자동으로 false 가 강제된다 (스크립트가 처리).
>
> **Web 빌드와 도메인**: Next.js `NEXT_PUBLIC_API_URL` 은 빌드 타임 환경
> 변수다. 도메인이 바뀌면 빌드 머신에서 `--build-arg NEXT_PUBLIC_API_URL=...`
> 로 다시 빌드해 새 이미지 태그로 반입해야 한다.

---

## 3. 운영 메모

- **이미지 갱신**: 번들을 새로 만들어 반입 → `load-bundle.sh` 다시 실행 →
  `docker compose up -d` (단일 노드) 또는 호스트별 `docker pull` 없이
  `docker run` 재실행. compose 는 imagedigest 가 바뀌면 컨테이너를 새로
  띄운다.
- **DB 마이그레이션**: 현재 EasyObs 는 첫 부팅에 SQLAlchemy `create_all` 로
  스키마를 만든다. 별도 alembic 마이그레이션은 없으므로, 메이저 버전 업
  시점에는 백업 후 갈아끼우는 흐름을 권장.
- **백업**: Postgres `pg_dump` + Blob (NFS/EFS 또는 호스트 named volume) 의
  주기적 스냅샷. 단일 노드라면 `docker run --rm -v easyobs_blob:/x ...` 등
  볼륨 백업.
- **로그**: 컨테이너 stdout 은 `docker logs` 또는 호스트 syslog 수집기로.
  파일로 직접 떨굴 필요가 있으면 `EASYOBS_LOG_FILE=/var/log/easyobs/api.log`
  + 그 경로를 호스트에 bind mount.
- **사설 레지스트리** 를 쓰는 환경이라면 번들의 tar 를 거치지 않고
  `docker tag easyobs/api:0.2.0 harbor.intra/easyobs/api:0.2.0 && docker push`
  로 올린 뒤, `.env` / Terraform 의 image tag 만 사설 주소로 바꾸면 된다.

---

## 4. 자주 발생하는 이슈

| 증상 | 원인 / 조치 |
|------|------|
| `docker load` 가 권한 거부 | sudo 필요. `EASYOBS_TARGET_DIR` 도 sudo 가 쓰는 경로면 동일. |
| 클러스터 leader 가 healthy 안됨 | RDS/외부 Postgres 가 도달 가능한지(Security group, route) 점검. `docker logs easyobs-api` 첫 1분치 확인. |
| 워커 N대인데 알람이 N번 와요 | 워커 컨테이너가 `EASYOBS_ALARM_ENABLED=true` 로 떠 있을 가능성. `docker exec easyobs-api env \| grep ALARM` 로 확인. |
| Web 콘솔이 API 호출에 실패 | `NEXT_PUBLIC_API_URL` 이 브라우저에서 도달 가능한 주소인가? 같은 도메인 LB 를 쓰면 path-based routing 으로 통합 권장. |
| 번들 sha256 불일치 | 전송 중 손상. `manifest.txt` 와 다시 비교 후 재전송. |

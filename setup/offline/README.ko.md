# EasyObs — 폐쇄망(오프라인) 배포

**영문 원문:** [`README.md`](README.md)

**빌드 머신(온라인):** 번들 생성. **대상 호스트(오프라인):** 이미지 로드 후 스크립트 실행.

번들: Docker 이미지 tar + `easyobs-source.tar.gz` + `easyobs-product.tar.gz` + 배포 스크립트. 프라이빗 레지스트리 없이 동작합니다.

---

## 번들 구성

| 산출물 | 출처 | 역할 |
|--------|------|------|
| `easyobs-images.tar` | `docker save` easyobs/api, easyobs/web | 앱 이미지 |
| `third-party-images.tar` | `docker save` postgres, nginx, … | 의존 이미지 |
| `easyobs-source.tar.gz` | 저장소 | 디버그 / 재빌드 |
| `easyobs-product.tar.gz` | `setup/` | Compose, Dockerfile, 스크립트 |
| `load-bundle.sh` | 번들에 포함 | `docker load` + 압축 해제 |
| `deploy-single.sh` | 번들에 포함 | 단일 노드 |
| `deploy-cluster.sh` | 번들에 포함 | 단일 호스트 / 멀티 호스트 |
| `manifest.txt` | 생성됨 | 파일 목록 + sha256 |

---

## 1. 빌드 머신

**필요:** Docker(24+), 저장소 체크아웃; `tar` 사용 가능(Linux/macOS/WSL; PowerShell 스크립트도 제공).

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

`./dist/easyobs-bundle/`를 폐쇄망으로 복사합니다. `manifest.txt`의 sha256으로 검증하세요.

릴리스 버전 관리 시 번들, `.env`, Terraform의 이미지 태그를 맞춥니다.

---

## 2. 대상 호스트

**필요:** Linux 호스트, Docker 24+ 및 Compose v2(필요 시 오프라인 패키지로 설치), docker 그룹 또는 `sudo docker`.

### 2-1. 번들 로드

```bash
cd /path/to/easyobs-bundle
./load-bundle.sh
```

동작: 두 tar `docker load`; 소스/제품을 `/opt/easyobs/{src,product}`에 풀기(덮어쓰기: `EASYOBS_TARGET_DIR=/data/easyobs ./load-bundle.sh`).

### 2-2-A. 단일 노드

```bash
./deploy-single.sh
```

`.env`가 없으면 `env.sample`로 생성; `EASYOBS_JWT_SECRET`, `POSTGRES_PASSWORD` 자동 채움 가능. deps + app compose 기동.

| 서비스 | URL |
|--------|-----|
| API | `http://<host>:8787` |
| OpenAPI | `http://<host>:8787/docs` |
| Web | `http://<host>:3000` |

`.env`에서 CORS / `NEXT_PUBLIC_API_URL`을 수정한 뒤 `docker compose ... up -d`로 다시 올립니다.

### 2-2-B. 단일 호스트 클러스터

```bash
./deploy-cluster.sh single-host 4
```

진입점: `http://<host>:80` (또는 `EASYOBS_LB_HTTP_PORT`).

### 2-2-C. 멀티 호스트 클러스터

전제: 공유 Postgres, 공유 blob 마운트(`EASYOBS_BLOB_HOST_DIR`), API 풀(및 Web) 앞의 LB.

```bash
export EASYOBS_DATABASE_URL='postgresql+asyncpg://easyobs:****@db.internal:5432/easyobs'
export EASYOBS_JWT_SECRET='<모든 호스트에서 동일>'
export EASYOBS_BLOB_HOST_DIR='/mnt/nfs/easyobs-blob/data'
export EASYOBS_PUBLIC_BASE_URL='http://easyobs.intra.example.com'

./deploy-cluster.sh multi-host leader   # 한 호스트
./deploy-cluster.sh multi-host worker   # N 호스트
./deploy-cluster.sh multi-host web
```

리더만: `EASYOBS_ALARM_ENABLED=true`(스크립트가 워커에서는 강제).

`NEXT_PUBLIC_API_URL`은 빌드 타임: 공개 API URL이 바뀌면 빌드 머신에서 올바른 `--build-arg`로 웹 이미지를 재빌드하고 번들을 다시 만듭니다.

---

## 3. 운영

- **업그레이드:** 새 번들 → `load-bundle.sh` → `docker compose up -d` / 배포 스크립트 재실행.
- **마이그레이션:** 스키마는 기동 시 SQLAlchemy `create_all`; 트리에 Alembic 없음 — 메이저 업그레이드는 백업/복원 계획.
- **백업:** `pg_dump` + blob(NFS/볼륨 스냅샷).
- **로그:** `docker logs` 또는 `EASYOBS_LOG_FILE` + 바인드 마운트.
- **프라이빗 레지스트리:** 선택 — load 후 `docker tag`/`push`, 배포 태그를 레지스트리로 지정.

---

## 4. 문제 해결

| 증상 | 확인 |
|------|------|
| `docker load` 권한 | `sudo` / docker 그룹 |
| 리더 unhealthy | DB 연결(SG/라우트); 첫 1분 `docker logs` |
| 알람 중복 | 워커에 `EASYOBS_ALARM_ENABLED=true` 금지 |
| Web이 API에 접근 불가 | 브라우저에서 접근 가능한 `NEXT_PUBLIC_API_URL` |
| sha256 불일치 | 전송 손상; `manifest.txt` 기준으로 재복사 |

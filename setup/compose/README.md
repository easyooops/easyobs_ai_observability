# EasyObs — Compose 운영 배포

이 디렉터리는 EasyObs(`docs/comparison/03.develop/easyobs/`) 를 운영 환경에
한 번에 띄우기 위한 Docker Compose 시리즈를 담는다.

| 파일 | 용도 |
|------|------|
| `docker-compose.deps.yml`    | Postgres (단일/클러스터/leader 공통) |
| `docker-compose.app.yml`     | 단일 노드: API + Web 1대씩 |
| `docker-compose.cluster.yml` | 단일 호스트 클러스터: API leader + API worker N대 + Web + nginx LB |
| `nginx.cluster.conf`         | 클러스터용 nginx 라우팅 (`/v1·/otlp·/healthz·/docs` → API, 그 외 → Web) |
| `env.sample`                 | `.env` 의 모든 변수 (단일/클러스터 공통) |

이미지는 워크스페이스 루트에서 한 번만 빌드한다.

```bash
docker build \
  -f docs/comparison/03.develop/easyobs/setup/images/api/Dockerfile \
  -t easyobs/api:0.2.0 \
  docs/comparison/03.develop/easyobs

docker build \
  -f docs/comparison/03.develop/easyobs/setup/images/web/Dockerfile \
  -t easyobs/web:0.2.0 \
  --build-arg NEXT_PUBLIC_API_URL=http://127.0.0.1:8787 \
  docs/comparison/03.develop/easyobs/apps/web
```

> 빌드 컨텍스트는 각각 **소스가 있는 디렉터리** 임에 주의. Compose 자체는
> `build:` 를 쓰지 않고 미리 빌드된 이미지를 참조한다 (오프라인 반입과
> 클러스터 푸시·배포 모두 이 방식이 가장 단순함).

## 1. 단일 노드 (Single)

가장 빠른 운영 시작. 한 VM 한 대에서 API+Web+Postgres 모두 띄움.

```bash
cd docs/comparison/03.develop/easyobs/setup/compose
cp env.sample .env

# .env 편집: POSTGRES_PASSWORD, EASYOBS_JWT_SECRET, NEXT_PUBLIC_API_URL ...
# 최소한 EASYOBS_JWT_SECRET 만은 반드시 강한 값으로 교체할 것:
#   openssl rand -hex 32

docker compose \
  -f docker-compose.deps.yml \
  -f docker-compose.app.yml \
  --env-file .env up -d
```

| 서비스 | URL |
|--------|-----|
| API     | `http://<host>:8787`    |
| OpenAPI | `http://<host>:8787/docs` |
| Web 콘솔 | `http://<host>:3000`    |

데이터/세션 정리(처음부터 다시 시작):

```bash
docker compose -f docker-compose.deps.yml -f docker-compose.app.yml down -v
```

## 2. 단일 호스트 클러스터 (Worker 수평 확장)

같은 VM 한 대에서 API 컨테이너 N대를 띄우고 nginx 가 분산. 트래픽이 늘 때
가장 빠른 확장 경로(코드 수정 없음).

```bash
cd docs/comparison/03.develop/easyobs/setup/compose
cp env.sample .env  # 이미 만들었으면 생략
# .env 의 EASYOBS_API_REPLICAS=4 로 워커 수 결정

docker compose \
  -f docker-compose.deps.yml \
  -f docker-compose.cluster.yml \
  --env-file .env up -d \
  --scale easyobs-api-worker=${EASYOBS_API_REPLICAS:-2}
```

진입점은 `http://<host>:80`(또는 .env 의 `EASYOBS_LB_HTTP_PORT`).

### 클러스터 운영 주의사항

1. **Alarm 중복 방지**: `easyobs-api-leader` 1대만 `EASYOBS_ALARM_ENABLED=true`.
   워커들은 `false` (compose 안에서 강제 설정). 이 규칙을 깨면 동일 알람이
   인스턴스 수만큼 발송된다.
2. **JWT 시크릿 공유**: `.env` 의 `EASYOBS_JWT_SECRET` 가 모든 컨테이너에
   동일해야 세션 토큰이 인스턴스 간 호환된다. 비워두면 첫 부팅에 자동 생성되어
   `easyobs_blob` named volume 에 저장되므로 같은 노드에서는 자연 공유되지만,
   여러 호스트로 확장하기 시작하면 반드시 명시 주입.
3. **Blob 공유**: `easyobs_blob` named volume 은 단일 호스트 안의 컨테이너들 끼리만
   공유된다. 여러 호스트로 가는 순간 NFS/EFS 또는 S3 어댑터가 필요.
   (현재 OSS 코드에는 S3 어댑터 미구현. EFS 마운트는 Terraform `cluster/` 가 처리.)
4. **DB**: SQLite 는 다중 라이터에 약하므로 `EASYOBS_DATABASE_URL` 을 반드시
   Postgres 로 둔다 (env.sample 기본값이 그럼). 더 큰 클러스터에서는 RDS 사용.

## 3. 외부 매니지드 Postgres 사용

`docker-compose.deps.yml` 을 빼고 `EASYOBS_DATABASE_URL` 만 RDS 등 외부 주소로
바꾸면 된다.

```bash
docker compose -f docker-compose.app.yml --env-file .env up -d
# 또는 클러스터:
docker compose -f docker-compose.cluster.yml --env-file .env up -d \
  --scale easyobs-api-worker=4
```

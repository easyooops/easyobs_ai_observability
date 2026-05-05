#!/usr/bin/env bash
# =============================================================================
# EasyObs offline — single-node deploy.
#
# load-bundle.sh 를 먼저 실행한 뒤 사용. /opt/easyobs/product/compose 디렉터리
# 에서 docker-compose.deps.yml + docker-compose.app.yml 로 API + Web + Postgres
# 1대씩을 띄운다.
# =============================================================================
set -euo pipefail

TARGET_DIR="${EASYOBS_TARGET_DIR:-/opt/easyobs}"
COMPOSE_DIR="$TARGET_DIR/product/compose"

if [ ! -d "$COMPOSE_DIR" ]; then
  echo "$COMPOSE_DIR not found. Run ./load-bundle.sh first." >&2
  exit 1
fi

cd "$COMPOSE_DIR"

# .env 가 없으면 sample 에서 복사하고 시크릿 자동 생성
if [ ! -f .env ]; then
  cp env.sample .env
  if command -v openssl >/dev/null 2>&1; then
    JWT_HEX=$(openssl rand -hex 32)
    PG_HEX=$(openssl rand -hex 16)
  else
    JWT_HEX=$(head -c 32 /dev/urandom | xxd -p -c 64)
    PG_HEX=$(head -c 16 /dev/urandom | xxd -p -c 32)
  fi
  sed -i \
    -e "s|EASYOBS_JWT_SECRET=replace-with-32-byte-hex-secret|EASYOBS_JWT_SECRET=$JWT_HEX|" \
    -e "s|POSTGRES_PASSWORD=change-me-postgres|POSTGRES_PASSWORD=$PG_HEX|" \
    -e "s|easyobs:change-me-postgres@postgres|easyobs:$PG_HEX@postgres|" \
    .env
  echo "[easyobs] created .env with auto-generated secrets"
  echo "  → POSTGRES_PASSWORD / EASYOBS_JWT_SECRET 가 무작위로 채워졌습니다. 백업 보관 권장."
fi

# 호스트 외부에서 콘솔에 접속할 때 NEXT_PUBLIC_API_URL 이 정확해야 하므로
# 운영 환경에 맞게 .env 의 NEXT_PUBLIC_API_URL / EASYOBS_CORS_ORIGINS 도
# 수정 후 다시 띄우는 것을 권장.

docker compose -f docker-compose.deps.yml -f docker-compose.app.yml --env-file .env up -d

echo
echo "[easyobs] containers:"
docker compose -f docker-compose.deps.yml -f docker-compose.app.yml --env-file .env ps
echo
echo "[easyobs] EasyObs is starting up. Default ports:"
echo "  API : http://<host>:8787"
echo "  Web : http://<host>:3000"
echo "    .env 의 EASYOBS_API_HOST_PORT / EASYOBS_WEB_HOST_PORT 로 변경 가능."

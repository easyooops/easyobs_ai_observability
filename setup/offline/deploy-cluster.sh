#!/usr/bin/env bash
# =============================================================================
# EasyObs offline — cluster deploy.
#
# 두 가지 클러스터 토폴로지를 지원한다 (선택은 첫 인자로):
#
#   ./deploy-cluster.sh single-host [REPLICAS]
#       한 호스트(같은 VM) 안에서 API leader 1 + API worker N + Web + nginx LB
#       를 docker-compose 로 띄운다. 가장 빠른 수평 확장.
#
#   ./deploy-cluster.sh multi-host ROLE
#       호스트 단위로 역할(role)을 분배해 컨테이너 1개씩 띄운다.
#       ROLE: leader | worker | web
#
#       multi-host 모드는 외부에서 다음 환경변수를 반드시 주입해야 한다:
#         EASYOBS_DATABASE_URL    (예: postgresql+asyncpg://user:pw@db-host:5432/easyobs)
#         EASYOBS_JWT_SECRET      (32 byte hex, 모든 호스트에 동일)
#         EASYOBS_BLOB_HOST_DIR   (NFS/EFS 마운트된 호스트 경로, 예: /mnt/efs/easyobs-blob/data)
#         EASYOBS_API_IMAGE       (기본 easyobs/api:0.2.0)
#         EASYOBS_WEB_IMAGE       (기본 easyobs/web:0.2.0)
#         EASYOBS_PUBLIC_BASE_URL (web 빌드/CORS 용, 예: http://easyobs.example.com)
# =============================================================================
set -euo pipefail

TARGET_DIR="${EASYOBS_TARGET_DIR:-/opt/easyobs}"
COMPOSE_DIR="$TARGET_DIR/product/compose"

MODE="${1:-single-host}"

if [ ! -d "$COMPOSE_DIR" ]; then
  echo "$COMPOSE_DIR not found. Run ./load-bundle.sh first." >&2
  exit 1
fi

case "$MODE" in
  single-host)
    REPLICAS="${2:-2}"
    cd "$COMPOSE_DIR"
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
        -e "s|EASYOBS_API_REPLICAS=2|EASYOBS_API_REPLICAS=$REPLICAS|" \
        .env
      echo "[easyobs] created .env (single-host cluster, replicas=$REPLICAS)"
    fi

    docker compose \
      -f docker-compose.deps.yml \
      -f docker-compose.cluster.yml \
      --env-file .env up -d \
      --scale "easyobs-api-worker=$REPLICAS"

    echo
    echo "[easyobs] cluster up. Entrypoint: http://<host>:80"
    docker compose \
      -f docker-compose.deps.yml \
      -f docker-compose.cluster.yml \
      --env-file .env ps
    ;;

  multi-host)
    ROLE="${2:-}"
    if [ -z "$ROLE" ]; then
      echo "Usage: $0 multi-host <leader|worker|web>" >&2
      exit 2
    fi

    : "${EASYOBS_API_IMAGE:=easyobs/api:0.2.0}"
    : "${EASYOBS_WEB_IMAGE:=easyobs/web:0.2.0}"

    case "$ROLE" in
      leader|worker)
        : "${EASYOBS_DATABASE_URL:?EASYOBS_DATABASE_URL must be set (postgresql+asyncpg://...)}"
        : "${EASYOBS_JWT_SECRET:?EASYOBS_JWT_SECRET must be set (32-byte hex)}"
        : "${EASYOBS_BLOB_HOST_DIR:?EASYOBS_BLOB_HOST_DIR must be set (shared NFS/EFS path)}"
        : "${EASYOBS_PUBLIC_BASE_URL:=http://localhost}"

        ALARM="false"
        [ "$ROLE" = "leader" ] && ALARM="true"

        docker rm -f easyobs-api 2>/dev/null || true
        docker run -d \
          --name easyobs-api \
          --restart unless-stopped \
          -p 8787:8787 \
          -v "$EASYOBS_BLOB_HOST_DIR":/var/lib/easyobs \
          -e EASYOBS_API_HOST=0.0.0.0 \
          -e EASYOBS_API_PORT=8787 \
          -e EASYOBS_DATA_DIR=/var/lib/easyobs \
          -e EASYOBS_DATABASE_URL="$EASYOBS_DATABASE_URL" \
          -e EASYOBS_JWT_SECRET="$EASYOBS_JWT_SECRET" \
          -e EASYOBS_LOG_FORMAT=json \
          -e EASYOBS_EVAL_ENABLED=true \
          -e EASYOBS_EVAL_AUTO_RULE_ON_INGEST=true \
          -e EASYOBS_ALARM_ENABLED="$ALARM" \
          -e EASYOBS_ALARM_EVAL_INTERVAL_SECONDS=60 \
          -e EASYOBS_CORS_ORIGINS="$EASYOBS_PUBLIC_BASE_URL" \
          "$EASYOBS_API_IMAGE"
        echo "[easyobs] $ROLE container up. alarm=$ALARM"
        ;;

      web)
        : "${EASYOBS_PUBLIC_BASE_URL:?EASYOBS_PUBLIC_BASE_URL must be set (http://easyobs.example.com)}"
        docker rm -f easyobs-web 2>/dev/null || true
        docker run -d \
          --name easyobs-web \
          --restart unless-stopped \
          -p 3000:3000 \
          -e NEXT_PUBLIC_API_URL="$EASYOBS_PUBLIC_BASE_URL" \
          "$EASYOBS_WEB_IMAGE"
        echo "[easyobs] web container up."
        echo "  주의: NEXT_PUBLIC_API_URL 은 빌드 시 정해지는 환경변수다. 다른 도메인을 쓰려면 빌드 머신에서"
        echo "        --build-arg NEXT_PUBLIC_API_URL=... 로 다시 빌드해 새 이미지를 반입할 것."
        ;;

      *)
        echo "Unknown role: $ROLE (leader|worker|web)" >&2
        exit 2
        ;;
    esac
    ;;

  *)
    sed -n '2,30p' "$0"
    exit 2
    ;;
esac

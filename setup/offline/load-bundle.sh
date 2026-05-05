#!/usr/bin/env bash
# =============================================================================
# EasyObs offline bundle — loader.
#
# 폐쇄망 호스트에서 번들 디렉터리 안에서 실행한다. tar 들을 docker 에 로드하고
# 소스/product 압축을 푼다 (deploy-*.sh 는 그 결과를 사용).
#
# 결과:
#   /opt/easyobs/src         <- easyobs 소스 트리
#   /opt/easyobs/product     <- compose, images Dockerfile, terraform 등
#   docker images            <- easyobs/api, easyobs/web, postgres, nginx
# =============================================================================
set -euo pipefail

BUNDLE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET_DIR="${EASYOBS_TARGET_DIR:-/opt/easyobs}"

if ! command -v docker >/dev/null 2>&1; then
  echo "docker is required on the target host." >&2
  exit 1
fi

if ! sudo -n true 2>/dev/null; then
  echo "[easyobs] sudo password may be required to write under $TARGET_DIR"
fi

echo "[easyobs] loading easyobs-images.tar"
sudo docker load -i "$BUNDLE_DIR/easyobs-images.tar"

if [ -f "$BUNDLE_DIR/third-party-images.tar" ]; then
  echo "[easyobs] loading third-party-images.tar"
  sudo docker load -i "$BUNDLE_DIR/third-party-images.tar"
fi

sudo mkdir -p "$TARGET_DIR/src" "$TARGET_DIR/product"
sudo chown -R "$USER":"$USER" "$TARGET_DIR" || true

echo "[easyobs] extracting source -> $TARGET_DIR/src"
tar -xzf "$BUNDLE_DIR/easyobs-source.tar.gz"  -C "$TARGET_DIR/src"  --strip-components=1

echo "[easyobs] extracting product -> $TARGET_DIR/product"
tar -xzf "$BUNDLE_DIR/easyobs-product.tar.gz" -C "$TARGET_DIR/product" --strip-components=1

echo
echo "[easyobs] loaded images:"
docker image ls | head -1
docker image ls | grep -E '^(easyobs|postgres|nginx)\b' || true

echo
echo "[easyobs] next steps:"
echo "  • single-node:  ./deploy-single.sh   (또는 cd $TARGET_DIR/product/compose && docker compose -f docker-compose.deps.yml -f docker-compose.app.yml --env-file .env up -d)"
echo "  • cluster:      ./deploy-cluster.sh  (한 호스트 안 N대 컨테이너 / 또는 호스트별 역할 지정)"

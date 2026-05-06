#!/usr/bin/env bash
# =============================================================================
# EasyObs — offline bundle builder.
#
# 인터넷이 되는 빌드 머신에서 실행해 폐쇄망으로 들고 갈 tar 번들을 만든다.
# 결과물 (모두 BUNDLE_DIR 안에):
#
#   easyobs-images.tar         # easyobs/api, easyobs/web (docker save 합본)
#   third-party-images.tar     # postgres:16, nginx:1.27-alpine
#   easyobs-source.tar.gz      # 소스 (setup/ 제외)
#   easyobs-product.tar.gz     # setup/ (compose+offline+...)
#   load-bundle.sh             # 폐쇄망 호스트가 실행할 로더
#   deploy-single.sh           # 단일 노드 자동 배포
#   deploy-cluster.sh          # 클러스터 leader/worker/web 역할 별 배포
#   README.md                  # 반입/배포 가이드
#   manifest.txt               # 산출물 목록 + sha256
#
# 사용:
#   cd <repo-root>
#   ./setup/offline/build-bundle.sh \
#       [--output ./dist/easyobs-bundle] \
#       [--api-tag easyobs/api:0.2.0] \
#       [--web-tag easyobs/web:0.2.0] \
#       [--postgres-image postgres:16] \
#       [--nginx-image nginx:1.27-alpine]
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PRODUCT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
SOURCE_DIR="$(cd "$PRODUCT_DIR/.." && pwd)"
REPO_ROOT="$SOURCE_DIR"

OUT="$REPO_ROOT/dist/easyobs-bundle"
API_TAG="easyobs/api:0.2.0"
WEB_TAG="easyobs/web:0.2.0"
POSTGRES_IMAGE="postgres:16"
NGINX_IMAGE="nginx:1.27-alpine"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --output)         OUT="$2"; shift 2 ;;
    --api-tag)        API_TAG="$2"; shift 2 ;;
    --web-tag)        WEB_TAG="$2"; shift 2 ;;
    --postgres-image) POSTGRES_IMAGE="$2"; shift 2 ;;
    --nginx-image)    NGINX_IMAGE="$2"; shift 2 ;;
    -h|--help)
      sed -n '2,30p' "$0"
      exit 0
      ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

if ! command -v docker >/dev/null 2>&1; then
  echo "docker is required on the build host." >&2
  exit 1
fi

mkdir -p "$OUT"

echo "[easyobs] building API image: $API_TAG"
docker build -f "$PRODUCT_DIR/images/api/Dockerfile" -t "$API_TAG" "$SOURCE_DIR"

echo "[easyobs] building Web image: $WEB_TAG"
docker build -f "$PRODUCT_DIR/images/web/Dockerfile" -t "$WEB_TAG" \
  --build-arg "NEXT_PUBLIC_API_URL=http://localhost:8787" \
  "$SOURCE_DIR/apps/web"

echo "[easyobs] pulling third-party images"
docker pull "$POSTGRES_IMAGE"
docker pull "$NGINX_IMAGE"

echo "[easyobs] saving easyobs-images.tar"
docker save -o "$OUT/easyobs-images.tar" "$API_TAG" "$WEB_TAG"

echo "[easyobs] saving third-party-images.tar"
docker save -o "$OUT/third-party-images.tar" "$POSTGRES_IMAGE" "$NGINX_IMAGE"

echo "[easyobs] packing source (setup/ 제외 — product archive 로 별도 패키징)"
tar -czf "$OUT/easyobs-source.tar.gz" \
  --exclude='.venv' --exclude='node_modules' --exclude='.next' \
  --exclude='__pycache__' --exclude='*.pyc' --exclude='data' \
  --exclude='setup' \
  -C "$(dirname "$SOURCE_DIR")" "$(basename "$SOURCE_DIR")"

echo "[easyobs] packing product (compose, offline scripts, terraform sans state)"
tar -czf "$OUT/easyobs-product.tar.gz" \
  --exclude='.terraform' --exclude='.terraform-staging' \
  --exclude='terraform.tfstate*' --exclude='tfplan' \
  -C "$(dirname "$PRODUCT_DIR")" "$(basename "$PRODUCT_DIR")"

# 폐쇄망 호스트가 그대로 실행할 수 있도록 loader/deploy 스크립트를 함께 동봉
cp "$PRODUCT_DIR/offline/load-bundle.sh"     "$OUT/load-bundle.sh"
cp "$PRODUCT_DIR/offline/deploy-single.sh"   "$OUT/deploy-single.sh"
cp "$PRODUCT_DIR/offline/deploy-cluster.sh"  "$OUT/deploy-cluster.sh"
cp "$PRODUCT_DIR/offline/README.md"          "$OUT/README.md"
chmod +x "$OUT/load-bundle.sh" "$OUT/deploy-single.sh" "$OUT/deploy-cluster.sh"

echo "[easyobs] writing manifest.txt"
{
  echo "EasyObs offline bundle"
  echo "Generated:        $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "API image tag:    $API_TAG"
  echo "Web image tag:    $WEB_TAG"
  echo "Postgres image:   $POSTGRES_IMAGE"
  echo "Nginx image:      $NGINX_IMAGE"
  echo
  echo "Files:"
  ( cd "$OUT" && find . -maxdepth 1 -type f -printf '  %P\n' | sort )
  echo
  echo "Checksums:"
  if command -v sha256sum >/dev/null 2>&1; then
    ( cd "$OUT" && sha256sum *.tar *.tar.gz *.sh README.md 2>/dev/null | sort )
  else
    ( cd "$OUT" && shasum -a 256 *.tar *.tar.gz *.sh README.md 2>/dev/null | sort )
  fi
} > "$OUT/manifest.txt"

echo
echo "[easyobs] done — bundle ready at: $OUT"
echo "  Copy this directory to your air-gapped host (USB, S3 transfer, scp ...)."
echo "  Then on the target host run:"
echo "    cd <bundle-dir>"
echo "    ./load-bundle.sh"
echo "    ./deploy-single.sh         # 또는 deploy-cluster.sh"

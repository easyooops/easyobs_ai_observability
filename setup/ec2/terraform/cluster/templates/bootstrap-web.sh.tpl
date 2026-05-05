#!/bin/bash
set -euo pipefail
exec > >(tee /var/log/easyobs-bootstrap.log | logger -t easyobs-bootstrap -s 2>/dev/console) 2>&1

export DEBIAN_FRONTEND=noninteractive

# 부팅 직후 unattended-upgrades / SSM patch baseline 가 dpkg 락을 점유할 수 있으므로
# 락이 풀릴 때까지 기다린 뒤에 apt 를 호출한다.
wait_apt_lock() {
  for _ in $(seq 1 90); do
    if ! fuser /var/lib/dpkg/lock-frontend /var/lib/apt/lists/lock /var/lib/dpkg/lock >/dev/null 2>&1; then
      return 0
    fi
    sleep 5
  done
  return 1
}
apt_run() {
  local attempt
  for attempt in 1 2 3 4 5; do
    wait_apt_lock || true
    if apt-get "$@"; then
      return 0
    fi
    echo "apt-get $* failed (attempt $attempt); retrying in 15s..." >&2
    sleep 15
  done
  return 1
}

apt_run update -y
apt_run install -y docker.io curl ca-certificates unzip awscli

systemctl enable --now docker
for i in $(seq 1 30); do
  docker info >/dev/null 2>&1 && break
  sleep 2
done

# ---- 소스 / product 다운로드 ----------------------------------------------
mkdir -p /opt/easyobs
cd /opt/easyobs
aws s3 cp "s3://${stage_bucket}/${source_object_key}"  ./easyobs-source.zip  --region "${aws_region}"
aws s3 cp "s3://${stage_bucket}/${product_object_key}" ./easyobs-product.zip --region "${aws_region}"

rm -rf src product
mkdir -p src product
unzip -q -o easyobs-source.zip  -d src
unzip -q -o easyobs-product.zip -d product

# ---- 이미지 빌드 -----------------------------------------------------------
# Web 은 ALB DNS 를 NEXT_PUBLIC_API_URL 로 박아 빌드해야 콘솔이 같은
# 도메인으로 API 를 호출한다 (ALB path-based routing 이 /v1·/otlp 를
# API 풀로 넘김).
docker build \
  -f product/images/web/Dockerfile \
  -t "${easyobs_web_image_tag}" \
  --build-arg "NEXT_PUBLIC_API_URL=http://${alb_dns}" \
  src/apps/web

# ---- 컨테이너 기동 ---------------------------------------------------------
docker rm -f easyobs-web 2>/dev/null || true

docker run -d \
  --name easyobs-web \
  --restart unless-stopped \
  -p 3000:3000 \
  -e NEXT_PUBLIC_API_URL="http://${alb_dns}" \
  "${easyobs_web_image_tag}"

# Health 대기
for i in $(seq 1 60); do
  if curl -fsS http://127.0.0.1:3000/ >/dev/null 2>&1; then
    break
  fi
  sleep 3
done

echo "EasyObs Web bootstrap finished. ALB: ${alb_dns}"

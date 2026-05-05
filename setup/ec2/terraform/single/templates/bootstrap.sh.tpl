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
apt_run install -y docker.io docker-compose-v2 curl ca-certificates unzip awscli

systemctl enable --now docker
for i in $(seq 1 30); do
  docker info >/dev/null 2>&1 && break
  sleep 2
done

# ---- 추가 데이터 볼륨(선택) ------------------------------------------------
if [ "${enable_data_volume}" = "true" ]; then
  DATA_DEV=""
  for cand in /dev/nvme1n1 /dev/xvdf /dev/sdf; do
    if [ -b "$cand" ]; then
      DATA_DEV="$cand"
      break
    fi
  done
  if [ -z "$DATA_DEV" ]; then
    echo "Extra data volume block device not found; using root disk for /mnt/data."
    mkdir -p /mnt/data
  else
    if ! blkid "$DATA_DEV" >/dev/null 2>&1; then
      mkfs.ext4 -F -L easyobs-data "$DATA_DEV"
    fi
    mkdir -p /mnt/data
    mount "$DATA_DEV" /mnt/data
    UUID=$(blkid -s UUID -o value "$DATA_DEV")
    if ! grep -q "$UUID" /etc/fstab; then
      echo "UUID=$UUID /mnt/data ext4 defaults,nofail 0 2" >> /etc/fstab
    fi
  fi
else
  mkdir -p /mnt/data
fi
chown root:root /mnt/data

# Docker named volume(`easyobs_blob`)을 /mnt/data 위에 두려면 docker daemon
# data-root 를 옮기거나 bind mount 를 사용해야 함. 여기서는 단순화를 위해
# /var/lib/docker 그대로 두고, /mnt/data 는 향후 호스트 백업용으로만 마운트.

# ---- IMDS / public IP ------------------------------------------------------
IMDS_TOKEN=$(curl -sS -X PUT "http://169.254.169.254/latest/api/token" -H "X-aws-ec2-metadata-token-ttl-seconds: 21600" || true)
if [ -n "$IMDS_TOKEN" ]; then
  PUBLIC_IP=$(curl -sS -H "X-aws-ec2-metadata-token: $IMDS_TOKEN" http://169.254.169.254/latest/meta-data/public-ipv4 || true)
else
  PUBLIC_IP=$(curl -sS http://169.254.169.254/latest/meta-data/public-ipv4 || true)
fi
if [ -z "$PUBLIC_IP" ]; then
  PUBLIC_IP="localhost"
fi

# ---- Stage 다운로드 + 압축 해제 -------------------------------------------
mkdir -p /opt/easyobs
cd /opt/easyobs
aws s3 cp "s3://${stage_bucket}/${source_object_key}"  ./easyobs-source.zip  --region "${aws_region}"
aws s3 cp "s3://${stage_bucket}/${product_object_key}" ./easyobs-product.zip --region "${aws_region}"

rm -rf src product
mkdir -p src product
unzip -q -o easyobs-source.zip  -d src
unzip -q -o easyobs-product.zip -d product

# ---- 이미지 빌드 -----------------------------------------------------------
docker build \
  -f product/images/api/Dockerfile \
  -t "${easyobs_api_image_tag}" \
  src

docker build \
  -f product/images/web/Dockerfile \
  -t "${easyobs_web_image_tag}" \
  --build-arg "NEXT_PUBLIC_API_URL=http://$PUBLIC_IP:${easyobs_api_port}" \
  src/apps/web

# ---- Compose deploy 디렉터리 구성 -----------------------------------------
DEPLOY_DIR=/opt/easyobs/deploy
mkdir -p "$DEPLOY_DIR"
cp product/compose/docker-compose.deps.yml "$DEPLOY_DIR/"
cp product/compose/docker-compose.app.yml  "$DEPLOY_DIR/"

cat > "$DEPLOY_DIR/.env" <<EOF
EASYOBS_API_IMAGE=${easyobs_api_image_tag}
EASYOBS_WEB_IMAGE=${easyobs_web_image_tag}
POSTGRES_IMAGE=postgres:16
NGINX_IMAGE=nginx:1.27-alpine

EASYOBS_API_HOST_PORT=${easyobs_api_port}
EASYOBS_WEB_HOST_PORT=${easyobs_web_port}

POSTGRES_USER=easyobs
POSTGRES_PASSWORD=${postgres_password}
POSTGRES_DB=easyobs

EASYOBS_DATABASE_URL=postgresql+asyncpg://easyobs:${postgres_password}@postgres:5432/easyobs
EASYOBS_JWT_SECRET=${jwt_secret}
EASYOBS_JWT_TTL_HOURS=12

EASYOBS_API_HOST=0.0.0.0
EASYOBS_API_PORT=8787
EASYOBS_DATA_DIR=/var/lib/easyobs
EASYOBS_LOG_LEVEL=INFO
EASYOBS_LOG_FORMAT=json
EASYOBS_LOG_REQUEST_BODY=false
EASYOBS_PRICING_SOURCE=auto
EASYOBS_CORS_ORIGINS=http://$PUBLIC_IP:${easyobs_web_port},http://$PUBLIC_IP:${easyobs_api_port}

EASYOBS_EVAL_ENABLED=true
EASYOBS_EVAL_AUTO_RULE_ON_INGEST=true

EASYOBS_ALARM_ENABLED=true
EASYOBS_ALARM_EVAL_INTERVAL_SECONDS=60

EASYOBS_SEED_MOCK_DATA=${seed_mock_data}
EASYOBS_SEED_MOCK_LIVE=${seed_mock_data}

NEXT_PUBLIC_API_URL=http://$PUBLIC_IP:${easyobs_api_port}
EOF
chmod 600 "$DEPLOY_DIR/.env"

# ---- 기동 ------------------------------------------------------------------
cd "$DEPLOY_DIR"
docker compose -f docker-compose.deps.yml -f docker-compose.app.yml --env-file .env pull --ignore-pull-failures || true
docker compose -f docker-compose.deps.yml -f docker-compose.app.yml --env-file .env up -d

# Health 대기
for i in $(seq 1 90); do
  API_HEALTH=$(docker inspect --format='{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' easyobs-api 2>/dev/null || true)
  if [ "$API_HEALTH" = "healthy" ]; then
    break
  fi
  sleep 2
done

# ---- Storage 설정 시드(첫 부팅에 한해) -------------------------------------
# admin UI 의 Storage 패널은 saved(/var/lib/easyobs/app_settings.json) 와
# active(env 의 EASYOBS_DATABASE_URL) 가 다르면 "Restart required" 가짜 경고를
#표시한다. 첫 부팅 시 active 값으로 saved 를 채워 두 값이 일치하게 만들어
# 운영자가 수동으로 'Save' 하지 않아도 되게 한다. 이미 파일이 있으면(=운영자가
# UI 에서 저장한 상태) 절대 덮어쓰지 않는다.
docker exec easyobs-api sh -c '
set -e
F=/var/lib/easyobs/app_settings.json
if [ -f "$F" ]; then
  exit 0
fi
mkdir -p /var/lib/easyobs
python - <<PY
import json, os, datetime
out = {
  "storage": {
    "blob": {
      "provider": "local",
      "path": os.environ.get("EASYOBS_DATA_DIR", "/var/lib/easyobs") + "/blob",
      "bucket": "", "prefix": "", "region": "",
      "s3_access_key_id": "", "s3_secret_access_key": "",
      "azure_account_name": "", "azure_account_key": "", "azure_container": "",
      "gcs_service_account_json": ""
    },
    "catalog": {
      "provider": "postgres",
      "sqlite_path": "",
      "pg_host": "postgres",
      "pg_port": 5432,
      "pg_database": os.environ.get("POSTGRES_DB", "easyobs"),
      "pg_user": os.environ.get("POSTGRES_USER", "easyobs"),
      "pg_password": os.environ.get("POSTGRES_PASSWORD", ""),
      "pg_sslmode": "prefer"
    },
    "retention": {"enabled": False, "days": 30}
  },
  "storage.meta": {
    "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    "updated_by": "bootstrap"
  }
}
with open("/var/lib/easyobs/app_settings.json", "w", encoding="utf-8") as f:
  json.dump(out, f, ensure_ascii=False, indent=2)
os.chmod("/var/lib/easyobs/app_settings.json", 0o600)
PY
' || echo "[warn] failed to seed app_settings.json (will fall back to UI Save)"

docker compose -f docker-compose.deps.yml -f docker-compose.app.yml --env-file .env ps

echo "EasyObs single-node bootstrap finished."
echo "  API : http://$PUBLIC_IP:${easyobs_api_port}"
echo "  Web : http://$PUBLIC_IP:${easyobs_web_port}"

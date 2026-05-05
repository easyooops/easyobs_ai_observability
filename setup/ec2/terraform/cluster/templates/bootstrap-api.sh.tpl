#!/bin/bash
set -euo pipefail
exec > >(tee /var/log/easyobs-bootstrap.log | logger -t easyobs-bootstrap -s 2>/dev/console) 2>&1

ROLE="${role}"          # leader | worker
ALARM_ENABLED="${alarm_enabled}"

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
apt_run install -y docker.io curl ca-certificates unzip awscli nfs-common

systemctl enable --now docker
for i in $(seq 1 30); do
  docker info >/dev/null 2>&1 && break
  sleep 2
done

# ---- EFS mount (blob 공유) -------------------------------------------------
mkdir -p /mnt/efs/easyobs-blob
EFS_DNS="${efs_id}.efs.${aws_region}.amazonaws.com"

# EFS mount target 가 ENI 등록을 끝낼 때까지 잠시 기다림
for i in $(seq 1 60); do
  if mount -t nfs4 -o nfsvers=4.1,rsize=1048576,wsize=1048576,hard,timeo=600,retrans=2,noresvport "$EFS_DNS:/" /mnt/efs/easyobs-blob; then
    break
  fi
  sleep 5
done

if ! grep -q "$EFS_DNS" /etc/fstab; then
  echo "$EFS_DNS:/ /mnt/efs/easyobs-blob nfs4 nfsvers=4.1,rsize=1048576,wsize=1048576,hard,timeo=600,retrans=2,noresvport,_netdev 0 0" >> /etc/fstab
fi

# 컨테이너 안의 easyobs 사용자가 쓸 수 있게 하위 디렉터리를 미리 만들어 권한 부여
mkdir -p /mnt/efs/easyobs-blob/data
chown 1100:1100 /mnt/efs/easyobs-blob/data

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
docker build \
  -f product/images/api/Dockerfile \
  -t "${easyobs_api_image_tag}" \
  src

# ---- 컨테이너 기동 ---------------------------------------------------------
# 클러스터에서는 docker-compose 가 아니라 단일 컨테이너만 띄운다.
# (한 호스트당 1 컨테이너). EFS 가 /var/lib/easyobs 에 마운트되어 모든
# API 호스트가 blob 을 공유한다.

CONTAINER_NAME="easyobs-api"

# 멱등성: 이미 떠 있으면 갈아치움 (user_data_replace_on_change 케이스)
docker rm -f "$CONTAINER_NAME" 2>/dev/null || true

docker run -d \
  --name "$CONTAINER_NAME" \
  --restart unless-stopped \
  -p 8787:8787 \
  -v /mnt/efs/easyobs-blob/data:/var/lib/easyobs \
  -e EASYOBS_API_HOST=0.0.0.0 \
  -e EASYOBS_API_PORT=8787 \
  -e EASYOBS_DATA_DIR=/var/lib/easyobs \
  -e EASYOBS_DATABASE_URL="postgresql+asyncpg://${rds_user}:${rds_password}@${rds_endpoint}:${rds_port}/${rds_db}" \
  -e EASYOBS_JWT_SECRET="${jwt_secret}" \
  -e EASYOBS_JWT_TTL_HOURS=12 \
  -e EASYOBS_LOG_LEVEL=INFO \
  -e EASYOBS_LOG_FORMAT=json \
  -e EASYOBS_LOG_REQUEST_BODY=false \
  -e EASYOBS_PRICING_SOURCE=auto \
  -e EASYOBS_CORS_ORIGINS="http://${alb_dns}" \
  -e EASYOBS_EVAL_ENABLED=true \
  -e EASYOBS_EVAL_AUTO_RULE_ON_INGEST=true \
  -e EASYOBS_ALARM_ENABLED="$ALARM_ENABLED" \
  -e EASYOBS_ALARM_EVAL_INTERVAL_SECONDS=60 \
  -e EASYOBS_SEED_MOCK_DATA="${seed_mock_data}" \
  -e EASYOBS_SEED_MOCK_LIVE="${seed_mock_data}" \
  "${easyobs_api_image_tag}"

# Health 대기
for i in $(seq 1 90); do
  if curl -fsS http://127.0.0.1:8787/healthz >/dev/null 2>&1; then
    break
  fi
  sleep 2
done

# ---- Storage 설정 시드(첫 부팅에 한해) -------------------------------------
# admin UI 의 Storage 패널은 saved(/var/lib/easyobs/app_settings.json) 와
# active(env 의 EASYOBS_DATABASE_URL) 가 다르면 "Restart required" 가짜 경고를
# 표시한다. 첫 부팅 시 active 값으로 saved 를 채워 두 값이 일치하게 만들어
# 운영자가 수동으로 'Save' 하지 않아도 되게 한다. 이미 파일이 있으면 보존.
SETTINGS_FILE=/mnt/efs/easyobs-blob/data/app_settings.json
if [ ! -f "$SETTINGS_FILE" ]; then
  python3 - <<PY
import json, os, datetime
out = {
  "storage": {
    "blob": {
      "provider": "local",
      "path": "/var/lib/easyobs/blob",
      "bucket": "", "prefix": "", "region": "",
      "s3_access_key_id": "", "s3_secret_access_key": "",
      "azure_account_name": "", "azure_account_key": "", "azure_container": "",
      "gcs_service_account_json": ""
    },
    "catalog": {
      "provider": "postgres",
      "sqlite_path": "",
      "pg_host": "${rds_endpoint}",
      "pg_port": ${rds_port},
      "pg_database": "${rds_db}",
      "pg_user": "${rds_user}",
      "pg_password": "${rds_password}",
      "pg_sslmode": "require"
    },
    "retention": {"enabled": False, "days": 30}
  },
  "storage.meta": {
    "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    "updated_by": "bootstrap"
  }
}
path = "$SETTINGS_FILE"
with open(path, "w", encoding="utf-8") as f:
  json.dump(out, f, ensure_ascii=False, indent=2)
os.chmod(path, 0o600)
PY
  chown 1100:1100 "$SETTINGS_FILE" 2>/dev/null || true
fi

echo "EasyObs API ($ROLE, alarm=$ALARM_ENABLED) bootstrap finished."

#!/usr/bin/env bash
# EasyObs cluster EC2 Terraform: init, validate, plan, interactive apply
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if ! command -v terraform >/dev/null 2>&1; then
  echo "terraform CLI is required: https://developer.hashicorp.com/terraform/install"
  exit 1
fi

mkdir -p .terraform-staging

terraform init -upgrade
terraform validate
terraform plan -out tfplan
echo
read -r -p "Apply this plan? [y/N] " ans
case "$ans" in
  y|Y|yes|YES) terraform apply tfplan ;;
  *) echo "Cancelled (tfplan file was left on disk)" ;;
esac

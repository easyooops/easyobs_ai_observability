#!/usr/bin/env bash
# EasyObs single-node EC2 Terraform: interactive destroy
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if ! command -v terraform >/dev/null 2>&1; then
  echo "terraform CLI is required: https://developer.hashicorp.com/terraform/install"
  exit 1
fi

terraform init -upgrade

echo
echo "This will destroy AWS resources created by this stack (VPC, EC2, EIP, S3 stage bucket, IAM roles, etc.)."
echo "Only resources recorded in terraform.tfstate will be removed."
echo
read -r -p "Run terraform destroy? [y/N] " ans
case "$ans" in
  y|Y|yes|YES) terraform destroy ;;
  *) echo "Cancelled" ;;
esac

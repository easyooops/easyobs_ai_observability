# EasyObs cluster EC2 Terraform: interactive destroy
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Get-Command terraform -ErrorAction SilentlyContinue)) {
    Write-Error "terraform CLI is required: https://developer.hashicorp.com/terraform/install"
    exit 1
}

terraform init -upgrade

Write-Host ""
Write-Host "This will destroy AWS resources created by this cluster (VPC, ALB, EC2, ASG, RDS, EFS, S3, IAM, etc.)."
Write-Host "Only resources recorded in terraform.tfstate will be removed."
Write-Host ""
$ans = Read-Host "Run terraform destroy? [y/N]"
switch -Regex ($ans) {
    "^(y|Y|yes|YES)$" { terraform destroy }
    default { Write-Host "Cancelled" }
}

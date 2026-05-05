# EasyObs cluster EC2 Terraform: init, validate, plan, interactive apply
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Get-Command terraform -ErrorAction SilentlyContinue)) {
    Write-Error "terraform CLI is required: https://developer.hashicorp.com/terraform/install"
    exit 1
}

if (-not (Test-Path -Path ".terraform-staging")) {
    New-Item -ItemType Directory -Path ".terraform-staging" | Out-Null
}

terraform init -upgrade
terraform validate
terraform plan -out tfplan

Write-Host ""
$ans = Read-Host "Apply this plan? [y/N]"
switch -Regex ($ans) {
    "^(y|Y|yes|YES)$" { terraform apply tfplan }
    default { Write-Host "Cancelled (tfplan file was left on disk)" }
}

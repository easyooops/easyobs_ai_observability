# =============================================================================
# EasyObs — offline bundle builder (PowerShell).
# build-bundle.sh 의 PowerShell 버전. Windows 빌드 머신에서 실행.
#
# 사용:
#   .\docs\comparison\03.develop\easyobs\setup\offline\build-bundle.ps1 `
#       -Output .\dist\easyobs-bundle `
#       -ApiTag easyobs/api:0.2.0 `
#       -WebTag easyobs/web:0.2.0
# =============================================================================
[CmdletBinding()]
param(
    [string]$Output         = "",
    [string]$ApiTag         = "easyobs/api:0.2.0",
    [string]$WebTag         = "easyobs/web:0.2.0",
    [string]$PostgresImage  = "postgres:16",
    [string]$NginxImage     = "nginx:1.27-alpine"
)

$ErrorActionPreference = "Stop"

# 이 스크립트 위치: docs\comparison\03.develop\easyobs\setup\offline\build-bundle.ps1
# → SourceDir  = docs\comparison\03.develop\easyobs (= setup 의 부모)
# → ProductDir = docs\comparison\03.develop\easyobs\setup
$ScriptDir  = Split-Path -Parent $PSCommandPath
$ProductDir = (Resolve-Path (Join-Path $ScriptDir "..")).Path
$SourceDir  = (Resolve-Path (Join-Path $ProductDir "..")).Path
$RepoRoot   = (Resolve-Path (Join-Path $SourceDir "..\..\..\..")).Path

if ([string]::IsNullOrEmpty($Output)) {
    $Output = Join-Path $RepoRoot "dist\easyobs-bundle"
}

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Error "docker is required on the build host."
    exit 1
}

if (-not (Test-Path $Output)) {
    New-Item -ItemType Directory -Path $Output | Out-Null
}

Write-Host "[easyobs] building API image: $ApiTag"
docker build -f (Join-Path $ProductDir "images\api\Dockerfile") -t $ApiTag $SourceDir
if ($LASTEXITCODE -ne 0) { throw "API build failed" }

Write-Host "[easyobs] building Web image: $WebTag"
docker build -f (Join-Path $ProductDir "images\web\Dockerfile") -t $WebTag `
    --build-arg "NEXT_PUBLIC_API_URL=http://localhost:8787" `
    (Join-Path $SourceDir "apps\web")
if ($LASTEXITCODE -ne 0) { throw "Web build failed" }

Write-Host "[easyobs] pulling third-party images"
docker pull $PostgresImage
docker pull $NginxImage

Write-Host "[easyobs] saving easyobs-images.tar"
docker save -o (Join-Path $Output "easyobs-images.tar") $ApiTag $WebTag

Write-Host "[easyobs] saving third-party-images.tar"
docker save -o (Join-Path $Output "third-party-images.tar") $PostgresImage $NginxImage

# tar 는 Windows 10 1903+ 기본 포함. 없는 환경이면 7-Zip / WSL 사용.
$tar = (Get-Command tar -ErrorAction SilentlyContinue)
if (-not $tar) {
    Write-Error "tar.exe (Windows 10 1903+) 또는 WSL/Git Bash 가 필요합니다. WSL 환경에선 build-bundle.sh 를 사용하세요."
    exit 1
}

Write-Host "[easyobs] packing source (setup/ 제외 — product archive 로 별도 패키징)"
tar -czf (Join-Path $Output "easyobs-source.tar.gz") `
    --exclude=".venv" --exclude="node_modules" --exclude=".next" `
    --exclude="__pycache__" --exclude="*.pyc" --exclude="data" `
    --exclude="setup" `
    -C (Split-Path $SourceDir -Parent) (Split-Path $SourceDir -Leaf)

Write-Host "[easyobs] packing product"
tar -czf (Join-Path $Output "easyobs-product.tar.gz") `
    --exclude=".terraform" --exclude=".terraform-staging" `
    --exclude="terraform.tfstate*" --exclude="tfplan" `
    -C (Split-Path $ProductDir -Parent) (Split-Path $ProductDir -Leaf)

Copy-Item (Join-Path $ProductDir "offline\load-bundle.sh")    (Join-Path $Output "load-bundle.sh")
Copy-Item (Join-Path $ProductDir "offline\deploy-single.sh")  (Join-Path $Output "deploy-single.sh")
Copy-Item (Join-Path $ProductDir "offline\deploy-cluster.sh") (Join-Path $Output "deploy-cluster.sh")
Copy-Item (Join-Path $ProductDir "offline\README.md")         (Join-Path $Output "README.md")

# Windows 호스트에서 줄바꿈이 CRLF 로 변환되지 않게 별도 처리는 git config 에 위임.

Write-Host "[easyobs] writing manifest.txt"
$manifest = @()
$manifest += "EasyObs offline bundle"
$manifest += "Generated:        $((Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ'))"
$manifest += "API image tag:    $ApiTag"
$manifest += "Web image tag:    $WebTag"
$manifest += "Postgres image:   $PostgresImage"
$manifest += "Nginx image:      $NginxImage"
$manifest += ""
$manifest += "Files:"
Get-ChildItem -Path $Output -File | ForEach-Object { $manifest += "  $($_.Name)" }
$manifest += ""
$manifest += "Checksums (SHA256):"
Get-ChildItem -Path $Output -File | Where-Object { $_.Name -ne "manifest.txt" } | ForEach-Object {
    $hash = (Get-FileHash $_.FullName -Algorithm SHA256).Hash.ToLower()
    $manifest += "  $hash  $($_.Name)"
}
$manifest | Set-Content -Path (Join-Path $Output "manifest.txt") -Encoding utf8

Write-Host ""
Write-Host "[easyobs] done — bundle ready at: $Output"
Write-Host "  Copy this directory to your air-gapped host (USB, S3, scp ...)."
Write-Host "  On the target host:  ./load-bundle.sh && ./deploy-single.sh"

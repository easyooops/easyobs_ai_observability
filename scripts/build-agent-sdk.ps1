<#
.SYNOPSIS
    Build easyobs_agent collection SDK as a lightweight wheel.
    Minimal package required for trace collection from services.

.DESCRIPTION
    Packages only the collection client (easyobs_agent), not the full EasyObs server.

    Build output:
        dist/agent/easyobs_agent-0.1.0-py3-none-any.whl

    Dependencies (3 OpenTelemetry packages):
        - opentelemetry-api
        - opentelemetry-sdk
        - opentelemetry-exporter-otlp-proto-http

    Use -IncludeDeps to also download the above dependencies into dist/agent/deps/.

.PARAMETER IncludeDeps
    Download SDK dependencies (OpenTelemetry) alongside the wheel.

.EXAMPLE
    .\scripts\build-agent-sdk.ps1
    .\scripts\build-agent-sdk.ps1 -IncludeDeps
#>
param(
    [switch]$IncludeDeps
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Push-Location $Root

try {
    $VenvPython = Join-Path $Root ".venv\Scripts\python.exe"
    if (-not (Test-Path $VenvPython)) {
        Write-Host "[build] .venv not found. Run 'python -m venv .venv' first." -ForegroundColor Red
        exit 1
    }

    $OutDir = Join-Path $Root "dist\agent"
    New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

    # Temporarily replace pyproject.toml with pyproject.agent.toml for build
    $MainToml = Join-Path $Root "pyproject.toml"
    $AgentToml = Join-Path $Root "pyproject.agent.toml"
    $BackupToml = Join-Path $Root "pyproject.toml.bak"

    Copy-Item $MainToml $BackupToml -Force
    Copy-Item $AgentToml $MainToml -Force

    try {
        Write-Host "[build] Building easyobs_agent SDK wheel..." -ForegroundColor Cyan
        & $VenvPython -m pip install --quiet build
        & $VenvPython -m build --outdir $OutDir --wheel .

        if ($LASTEXITCODE -ne 0) {
            Write-Host "[build] Build failed." -ForegroundColor Red
            exit 1
        }
    } finally {
        # Restore original pyproject.toml
        Move-Item $BackupToml $MainToml -Force
    }

    Write-Host "[build] Build succeeded:" -ForegroundColor Green
    Get-ChildItem $OutDir\*.whl | Format-Table Name, @{N="Size(KB)";E={[math]::Round($_.Length/1024,1)}}

    if ($IncludeDeps) {
        $DepsDir = Join-Path $OutDir "deps"
        New-Item -ItemType Directory -Force -Path $DepsDir | Out-Null
        Write-Host "[build] Downloading OpenTelemetry dependencies..." -ForegroundColor Cyan
        & $VenvPython -m pip download --dest $DepsDir `
            "opentelemetry-api>=1.28.0" `
            "opentelemetry-sdk>=1.28.0" `
            "opentelemetry-exporter-otlp-proto-http>=1.28.0"
        Write-Host "[build] Dependencies downloaded:" -ForegroundColor Green
        Get-ChildItem $DepsDir\*.whl | Format-Table Name, @{N="Size(KB)";E={[math]::Round($_.Length/1024,1)}}
    }

    Write-Host ""
    Write-Host "=== Install on service (air-gapped) ===" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "Required files:" -ForegroundColor White
    Write-Host "  dist/agent/easyobs_agent-0.1.0-py3-none-any.whl  (~15KB)"
    if ($IncludeDeps) {
        Write-Host "  dist/agent/deps/*.whl                            (OpenTelemetry)"
    }
    Write-Host ""
    Write-Host "Install command:" -ForegroundColor White
    if ($IncludeDeps) {
        Write-Host "  pip install --no-index --find-links ./deps ./easyobs_agent-0.1.0-py3-none-any.whl"
    } else {
        Write-Host "  pip install easyobs_agent-0.1.0-py3-none-any.whl"
    }
    Write-Host ""
    Write-Host "In your service code:" -ForegroundColor White
    Write-Host '  from easyobs_agent import init, traced'
    Write-Host '  init("http://<easyobs-host>:8787", token="eobs_...", service="my-svc")'
    Write-Host ""
} finally {
    Pop-Location
}

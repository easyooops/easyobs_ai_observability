$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$body = Get-Content -Raw (Join-Path $PSScriptRoot "sample_ingest.json")
if (-not $env:EASYOBS_INGEST_TOKEN) {
  Write-Error "Set `$env:EASYOBS_INGEST_TOKEN to a service token (eobs_…) — mint one in the UI under Setup > Organizations > <org> > Services."
  exit 2
}
$token = $env:EASYOBS_INGEST_TOKEN
$port = if ($env:EASYOBS_API_PORT) { $env:EASYOBS_API_PORT } else { "8787" }
Invoke-RestMethod `
  -Uri "http://127.0.0.1:$port/otlp/v1/traces" `
  -Method Post `
  -Headers @{ Authorization = "Bearer $token" } `
  -ContentType "application/json" `
  -Body $body

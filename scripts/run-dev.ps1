# EasyObs: API + Next dev server in one terminal (Windows PowerShell 5.1+)
#
# Behaviour:
#   * API starts via uvicorn --reload, with logs streaming live into THIS
#     terminal AND mirrored to data\api.log for after-the-fact debugging.
#   * Next.js dev server runs in the foreground, also visible in this terminal.
#   * Ctrl+C stops both — including any child python.exe (uvicorn worker) that
#     would otherwise become an orphan and keep port 8787 in LISTEN state.
#
# Usage:
#   .\scripts\run-dev.ps1
#   .\scripts\run-dev.ps1 -ApiPort 8787 -WebPort 3000 -SkipInstall
#   .\scripts\run-dev.ps1 -LogFormat json   # JSON logs (CloudWatch-style)

[CmdletBinding()]
param(
    [int]$ApiPort = 8787,
    [int]$WebPort = 3000,
    [switch]$SkipInstall,
    [ValidateSet("console", "json")]
    [string]$LogFormat = "console",
    [ValidateSet("DEBUG", "INFO", "WARNING", "ERROR")]
    [string]$LogLevel = "INFO"
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $RepoRoot

# Force UTF-8 so non-ASCII log lines render correctly inside Windows Terminal /
# Cursor terminal (default cp949 mangles JSON payloads and Korean messages).
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"
$env:PYTHONDONTWRITEBYTECODE = "1"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

if (-not (Test-Path .env) -and (Test-Path .env.sample)) {
    Copy-Item .env.sample .env
    Write-Host "[easyobs] Created .env from .env.sample"
}

if (-not $SkipInstall) {
    if (Get-Command uv -ErrorAction SilentlyContinue) {
        Write-Host "[easyobs] uv sync --extra agent"
        uv sync --extra agent
    }
    else {
        if (-not (Test-Path .venv)) {
            Write-Host "[easyobs] python -m venv .venv"
            python -m venv .venv
        }
        $pip = Join-Path $RepoRoot ".venv\Scripts\pip.exe"
        Write-Host "[easyobs] pip install -e '.[agent]'"
        & $pip install -e ".[agent]"
    }
}

$python = Join-Path $RepoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    throw "No .venv\Scripts\python.exe. Run once without -SkipInstall (install uv or Python venv)."
}

# Clear __pycache__ under src/ to ensure fresh bytecode (avoids stale .pyc
# from prior runs shadowing source changes on Windows where watchfiles
# reloader can be unreliable).
Get-ChildItem -Path (Join-Path $RepoRoot "src") -Directory -Recurse -Filter "__pycache__" -ErrorAction SilentlyContinue |
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
Write-Host "[easyobs] Cleared __pycache__ under src/"

$env:EASYOBS_API_PORT = "$ApiPort"
$env:NEXT_PUBLIC_API_URL = "http://127.0.0.1:$ApiPort"
$env:EASYOBS_LOG_FORMAT = $LogFormat
$env:EASYOBS_LOG_LEVEL = $LogLevel
# Force local source tree precedence over any stale editable/site-packages.
$env:PYTHONPATH = (Join-Path $RepoRoot "src")

# ----------------------------------------------------------------------------
# Cleanup helpers (must aggressively kill orphaned uvicorn workers AND their
# multiprocessing children — Windows otherwise leaves a phantom LISTEN socket
# on the API port and an open file handle on data\api.log).
# ----------------------------------------------------------------------------
function Get-DescendantPids([int]$rootPid) {
    $all = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue
    $byParent = @{}
    foreach ($p in $all) {
        if (-not $byParent.ContainsKey([int]$p.ParentProcessId)) {
            $byParent[[int]$p.ParentProcessId] = New-Object System.Collections.Generic.List[int]
        }
        $byParent[[int]$p.ParentProcessId].Add([int]$p.ProcessId)
    }
    $out = New-Object System.Collections.Generic.List[int]
    $queue = New-Object System.Collections.Generic.Queue[int]
    $queue.Enqueue($rootPid)
    while ($queue.Count -gt 0) {
        $cur = $queue.Dequeue()
        if ($byParent.ContainsKey($cur)) {
            foreach ($child in $byParent[$cur]) {
                if (-not $out.Contains($child)) {
                    $out.Add($child)
                    $queue.Enqueue($child)
                }
            }
        }
    }
    return $out
}

function Stop-EasyobsApiProcesses {
    # 1) Find uvicorn parents (the only ones whose CommandLine still mentions
    #    the module name; their multiprocessing children only show
    #    `spawn_main` and would slip through a CommandLine match).
    $parents = Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -and ($_.CommandLine -match 'uvicorn' -and $_.CommandLine -match 'easyobs') }

    # 1b) Also find orphaned multiprocessing spawn workers whose parents are dead
    #     (these linger on Windows and hold the port open with stale code).
    $spawns = Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -and $_.CommandLine -match 'multiprocessing\.spawn' } |
        Where-Object { -not (Get-Process -Id $_.ParentProcessId -ErrorAction SilentlyContinue) }

    # 2) Build the full kill list (parent + descendants), de-duped.
    $kill = New-Object System.Collections.Generic.HashSet[int]
    foreach ($p in $parents) {
        [void]$kill.Add([int]$p.ProcessId)
        foreach ($d in (Get-DescendantPids -rootPid ([int]$p.ProcessId))) {
            [void]$kill.Add([int]$d)
        }
    }
    foreach ($s in $spawns) {
        [void]$kill.Add([int]$s.ProcessId)
    }
    # 3) Anything still bound to the port goes too (covers reused PIDs after
    #    a crash where the parent record is gone but the socket is held).
    Get-NetTCPConnection -LocalPort $ApiPort -State Listen -ErrorAction SilentlyContinue |
        ForEach-Object { [void]$kill.Add([int]$_.OwningProcess) }

    # 3b) Also use netstat as fallback (Get-NetTCPConnection can miss entries).
    try {
        netstat -ano 2>$null | Select-String ":$ApiPort\s.*LISTENING" | ForEach-Object {
            $parts = $_.ToString().Trim() -split '\s+'
            $portPid = [int]$parts[-1]
            if ($portPid -gt 4) { [void]$kill.Add($portPid) }
        }
    } catch {}

    foreach ($victimId in $kill) {
        # Skip our own PID and the PowerShell host (defensive).
        if ($victimId -eq $PID) { continue }
        try {
            Stop-Process -Id $victimId -Force -ErrorAction Stop
            Write-Host "[easyobs] killed orphan pid=$victimId"
        }
        catch {}
    }

    # 4) Wait briefly for the OS to release the file/port handles.
    if ($kill.Count -gt 0) { Start-Sleep -Milliseconds 800 }
}

function Stop-EasyobsWebProcesses {
    # `next dev` on Windows survives a Ctrl+C from the parent shell about
    # half the time -- the cmd.exe wrapper exits but the node.exe child
    # keeps the LISTEN socket on $WebPort. Kill anything still bound to
    # the port plus its descendants so a re-run never hits EADDRINUSE.
    $kill = New-Object System.Collections.Generic.HashSet[int]
    Get-NetTCPConnection -LocalPort $WebPort -State Listen -ErrorAction SilentlyContinue |
        ForEach-Object {
            $owner = [int]$_.OwningProcess
            [void]$kill.Add($owner)
            foreach ($d in (Get-DescendantPids -rootPid $owner)) {
                [void]$kill.Add([int]$d)
            }
        }

    foreach ($victimId in $kill) {
        if ($victimId -eq $PID) { continue }
        try {
            Stop-Process -Id $victimId -Force -ErrorAction Stop
            Write-Host "[easyobs] killed orphan web pid=$victimId (port $WebPort)"
        }
        catch {}
    }

    if ($kill.Count -gt 0) { Start-Sleep -Milliseconds 500 }
}

function Reset-LogFile([string]$path) {
    # Try to truncate; if some lingering process still holds the handle, we
    # fall back to appending a single banner so the script can keep going.
    for ($i = 0; $i -lt 5; $i++) {
        try {
            Set-Content -Path $path -Value "" -Encoding utf8 -ErrorAction Stop
            return
        }
        catch {
            Start-Sleep -Milliseconds 400
        }
    }
    Write-Host "[easyobs] WARN: could not truncate $path (still locked); appending instead"
    try {
        Add-Content -Path $path -Value "`n--- run-dev.ps1 restart $(Get-Date -Format 'o') ---" -Encoding utf8 -ErrorAction Stop
    }
    catch {}
}

Stop-EasyobsApiProcesses
Stop-EasyobsWebProcesses

$logDir = Join-Path $RepoRoot "data"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$apiLog = Join-Path $logDir "api.log"
$apiErr = Join-Path $logDir "api.err.log"
Reset-LogFile $apiLog
Reset-LogFile $apiErr

$apiProc = $null
$tailJob = $null
try {
    Write-Host "[easyobs] Starting API on http://127.0.0.1:$ApiPort (log_format=$LogFormat, level=$LogLevel)"
    Write-Host "[easyobs] API logs -> $apiLog (also streaming into this terminal)"

    $apiProc = Start-Process -FilePath $python -ArgumentList @(
        "-u", "-B",
        "-m", "uvicorn", "easyobs.http_app:create_app",
        "--factory", "--host", "127.0.0.1", "--port", "$ApiPort",
        "--no-access-log"
    ) -WorkingDirectory $RepoRoot -PassThru -WindowStyle Hidden `
      -RedirectStandardOutput $apiLog -RedirectStandardError $apiErr

    # Background job: tail both api.log and api.err.log into the foreground
    # terminal so the developer sees uvicorn startup + every request live.
    $tailJob = Start-Job -Name "easyobs-api-log" -ScriptBlock {
        param($outPath, $errPath)
        for ($i = 0; $i -lt 25 -and -not (Test-Path $outPath); $i++) { Start-Sleep -Milliseconds 200 }
        $jobs = @()
        if (Test-Path $outPath) {
            $jobs += Start-Job -ScriptBlock { param($p) Get-Content -Path $p -Wait -Tail 0 } -ArgumentList $outPath
        }
        if (Test-Path $errPath) {
            $jobs += Start-Job -ScriptBlock { param($p) Get-Content -Path $p -Wait -Tail 0 | ForEach-Object { "[stderr] $_" } } -ArgumentList $errPath
        }
        try {
            while ($true) {
                foreach ($j in $jobs) {
                    Receive-Job $j -ErrorAction SilentlyContinue
                }
                Start-Sleep -Milliseconds 150
            }
        }
        finally {
            foreach ($j in $jobs) { Stop-Job $j -ErrorAction SilentlyContinue; Remove-Job $j -Force -ErrorAction SilentlyContinue }
        }
    } -ArgumentList $apiLog, $apiErr

    # Pump tail-job output into the host while we wait for /healthz.
    $ready = $false
    for ($i = 0; $i -lt 60; $i++) {
        Receive-Job $tailJob -ErrorAction SilentlyContinue | ForEach-Object { Write-Host $_ }
        try {
            Invoke-RestMethod -Uri "http://127.0.0.1:$ApiPort/healthz" -TimeoutSec 1 | Out-Null
            $ready = $true
            break
        }
        catch {
            Start-Sleep -Milliseconds 400
        }
    }
    Receive-Job $tailJob -ErrorAction SilentlyContinue | ForEach-Object { Write-Host $_ }
    if (-not $ready) {
        throw "API did not become ready on http://127.0.0.1:$ApiPort/healthz (see $apiErr)"
    }
    Write-Host "[easyobs] API OK -- docs: http://127.0.0.1:$ApiPort/docs"

    $webDir = Join-Path $RepoRoot "apps\web"
    if (-not (Test-Path (Join-Path $webDir "node_modules"))) {
        Write-Host "[easyobs] npm install (apps/web)"
        Push-Location $webDir
        npm install
        Pop-Location
    }
    if (-not (Test-Path (Join-Path $webDir ".env.local")) -and (Test-Path (Join-Path $webDir ".env.sample"))) {
        Copy-Item (Join-Path $webDir ".env.sample") (Join-Path $webDir ".env.local")
        Write-Host "[easyobs] Created apps/web/.env.local from .env.sample"
    }

    Write-Host "[easyobs] UI - http://localhost:$WebPort (Ctrl+C stops API and exits)"
    Push-Location $webDir
    $nextBin = Join-Path $webDir "node_modules\.bin\next.cmd"
    if (-not (Test-Path $nextBin)) {
        throw "next CLI not found at $nextBin (try removing -SkipInstall to run npm install)"
    }

    # Run a tiny background pump that drains the API tail-job into the host
    # *while* `next dev` is in the foreground. It's a separate job so it can
    # write to the terminal without competing with next's stdout.
    $pumpJob = Start-Job -Name "easyobs-log-pump" -ScriptBlock {
        param($jobName)
        while ($true) {
            $j = Get-Job -Name $jobName -ErrorAction SilentlyContinue
            if (-not $j) { break }
            Receive-Job $j -ErrorAction SilentlyContinue | ForEach-Object {
                # Color-code error/warning lines for quick scanning.
                if ($_ -match 'ERROR|exc_info|Traceback|\[stderr\]') {
                    Write-Host $_ -ForegroundColor Red
                }
                elseif ($_ -match 'WARNING|-> 4\d\d ') {
                    Write-Host $_ -ForegroundColor Yellow
                }
                else {
                    Write-Host $_ -ForegroundColor DarkCyan
                }
            }
            Start-Sleep -Milliseconds 200
        }
    } -ArgumentList $tailJob.Name

    try {
        & $nextBin dev --port $WebPort
    }
    finally {
        if ($pumpJob) {
            Stop-Job $pumpJob -ErrorAction SilentlyContinue
            Remove-Job $pumpJob -Force -ErrorAction SilentlyContinue
        }
        Pop-Location
    }
}
finally {
    if ($tailJob) {
        Stop-Job $tailJob -ErrorAction SilentlyContinue
        Remove-Job $tailJob -Force -ErrorAction SilentlyContinue
    }
    if ($apiProc -and -not $apiProc.HasExited) {
        try { Stop-Process -Id $apiProc.Id -Force -ErrorAction SilentlyContinue } catch {}
        Write-Host "[easyobs] API process stopped."
    }
    Stop-EasyobsApiProcesses
    Stop-EasyobsWebProcesses
}

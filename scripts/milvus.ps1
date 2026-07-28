param(
    [ValidateSet("start", "stop", "status", "logs")]
    [string]$Action = "status"
)

$ErrorActionPreference = "Stop"
[Console]::InputEncoding = [Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)

$projectRoot = Split-Path -Parent $PSScriptRoot
$composePath = Join-Path $projectRoot "deploy\milvus\compose.yaml"
$stateDirectory = Join-Path $projectRoot "storage\milvus"
$keepAlivePidPath = Join-Path $stateDirectory "wsl-keepalive.pid"
if (-not (Test-Path -LiteralPath $composePath -PathType Leaf)) {
    throw "Milvus Compose file is unavailable"
}

$wslComposePath = (& wsl.exe --exec wslpath -a $composePath).Trim()
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($wslComposePath)) {
    throw "Could not resolve the Compose path inside WSL"
}

function Test-KeepAliveProcess([int]$ProcessId) {
    $process = Get-CimInstance Win32_Process -Filter "ProcessId = $ProcessId" -ErrorAction SilentlyContinue
    return $null -ne $process -and $process.Name -eq "wsl.exe" -and
        $process.CommandLine -match "--exec\s+sleep\s+infinity"
}

function Start-WslKeepAlive {
    New-Item -ItemType Directory -Path $stateDirectory -Force | Out-Null
    if (Test-Path -LiteralPath $keepAlivePidPath -PathType Leaf) {
        $stored = 0
        if ([int]::TryParse((Get-Content -LiteralPath $keepAlivePidPath -Raw).Trim(), [ref]$stored) -and
            (Test-KeepAliveProcess $stored)) {
            return
        }
        Remove-Item -LiteralPath $keepAlivePidPath -Force
    }
    $process = Start-Process -FilePath "wsl.exe" -ArgumentList @(
        "--exec", "sleep", "infinity"
    ) -WindowStyle Hidden -PassThru
    Start-Sleep -Milliseconds 500
    if ($process.HasExited) {
        throw "Could not keep the WSL distribution running"
    }
    [IO.File]::WriteAllText($keepAlivePidPath, "$($process.Id)`n", [Text.UTF8Encoding]::new($false))
}

switch ($Action) {
    "start" {
        Start-WslKeepAlive
        & wsl.exe --exec docker compose -f $wslComposePath up -d --wait
    }
    "stop" {
        & wsl.exe --exec docker compose -f $wslComposePath stop
    }
    "status" {
        & wsl.exe --exec docker compose -f $wslComposePath ps
    }
    "logs" {
        & wsl.exe --exec docker compose -f $wslComposePath logs --tail 200
    }
}
if ($LASTEXITCODE -ne 0) {
    throw "Milvus Compose action failed"
}

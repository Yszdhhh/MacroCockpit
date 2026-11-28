param(
    [string]$HostAddr = "127.0.0.1",
    [int]$Port = 8788,
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"
$env:PYTHONUTF8 = "1"
$env:MACROCOCKPIT_ROOT = $PSScriptRoot
Set-Location -LiteralPath $PSScriptRoot

$pyExe = if ($env:LOCALAPPDATA) { Join-Path $env:LOCALAPPDATA "Programs\Python\Python313\python.exe" } else { "" }
if (-not $pyExe -or -not (Test-Path $pyExe)) {
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if (-not $cmd) {
        Write-Error "未检测到 Python 环境。"
        exit 1
    }
    $pyExe = $cmd.Source
}

$url = "http://$HostAddr`:$Port/"
Write-Host "启动 MacroCockpit 本地免密驾驶舱：$url" -ForegroundColor Cyan
if (-not $NoBrowser) {
    Start-Job -ScriptBlock { param($u) Start-Sleep -Seconds 2; Start-Process $u } -ArgumentList $url | Out-Null
}

& $pyExe -m backend.server --host $HostAddr --port $Port --role standalone --no-auth

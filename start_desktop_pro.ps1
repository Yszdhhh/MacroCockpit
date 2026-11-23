param(
    [string]$HostAddr = "127.0.0.1",
    [int]$Port = 18790,
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"
$env:PYTHONUTF8 = "1"
$env:MACROCOCKPIT_ROOT = $PSScriptRoot
Set-Location -LiteralPath $PSScriptRoot

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Write-Error "未检测到 Python 环境，请先安装 Python 3.11+。"
    exit 1
}

$url = "http://$HostAddr`:$Port/"
Write-Host "启动 MacroCockpit 桌面专业终端：$url" -ForegroundColor Cyan
if (-not $NoBrowser) {
    Start-Job -ScriptBlock { param($u) Start-Sleep -Seconds 2; Start-Process $u } -ArgumentList $url | Out-Null
}

& python -m desktop.pro_server --host $HostAddr --port $Port

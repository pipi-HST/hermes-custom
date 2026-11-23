<#
.SYNOPSIS
    One-command launcher for Hermes Knowledge (Knowledge Service + Web Dashboard).
#>
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

$RuntimeRepoUrl = if ($env:HERMES_RUNTIME_REPO) { $env:HERMES_RUNTIME_REPO } else { "https://github.com/pipi-HST/hermes-agent-main.git" }
$SiblingRuntime = Join-Path (Split-Path -Parent $Root) "hermes-agent-main"
$LocalRuntime = Join-Path $Root ".runtime\hermes-agent-main"
$RuntimeDir = if (Test-Path (Join-Path $SiblingRuntime "hermes_cli\main.py")) { $SiblingRuntime } else { $LocalRuntime }

function Find-Python {
    if (Get-Command py -ErrorAction SilentlyContinue) {
        try {
            & py -3.12 -c "import sys; assert sys.version_info >= (3, 12)" 2>$null
            if ($LASTEXITCODE -eq 0) { return @("py", "-3.12") }
        } catch {}
    }
    if (Get-Command python -ErrorAction SilentlyContinue) {
        try {
            & python -c "import sys; assert sys.version_info >= (3, 12)" 2>$null
            if ($LASTEXITCODE -eq 0) { return @("python") }
        } catch {}
    }
    throw "未找到 Python 3.12+，请先前往 https://www.python.org/downloads/ 安装（安装时勾选 Add Python to PATH）。"
}

function Load-DotEnv([string]$Path) {
    $map = @{}
    if (-not (Test-Path $Path)) { return $map }
    foreach ($raw in Get-Content -Path $Path -Encoding UTF8) {
        $line = $raw.Trim()
        if (-not $line -or $line.StartsWith("#") -or -not $line.Contains("=")) { continue }
        $idx = $line.IndexOf("=")
        $k = $line.Substring(0, $idx).Trim()
        $v = $line.Substring($idx + 1).Trim()
        if ($v.Length -ge 2 -and (($v.StartsWith('"') -and $v.EndsWith('"')) -or ($v.StartsWith("'") -and $v.EndsWith("'")))) {
            $v = $v.Substring(1, $v.Length - 2)
        }
        $map[$k] = $v
    }
    return $map
}

Write-Host "[Hermes] 正在检查本地运行环境..." -ForegroundColor Cyan

# 1. Ensure runtime checkout exists
if (-not (Test-Path (Join-Path $RuntimeDir "hermes_cli\main.py"))) {
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
        throw "未找到 Git，请先安装 Git 后重试。"
    }
    Write-Host "[Hermes] 首次运行：正在下载网页工作台运行时到 .runtime ..." -ForegroundColor Cyan
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $RuntimeDir) | Out-Null
    & git clone --depth 1 $RuntimeRepoUrl $RuntimeDir
    if ($LASTEXITCODE -ne 0) { throw "下载运行时仓库失败，请检查网络或代理设置。" }
}

# 2. Ensure shared Python venv in RuntimeDir (or custom .venv if already present)
$VenvPython = Join-Path $RuntimeDir ".venv\Scripts\python.exe"
$DepsMarker = Join-Path $RuntimeDir ".venv\.hermes_bundle_deps_ok"
if (-not (Test-Path $VenvPython)) {
    $pyCmd = Find-Python
    Write-Host "[Hermes] 首次运行：正在创建 Python 虚拟环境..." -ForegroundColor Cyan
    if ($pyCmd.Length -eq 2) {
        & $pyCmd[0] $pyCmd[1] -m venv (Join-Path $RuntimeDir ".venv")
    } else {
        & $pyCmd[0] -m venv (Join-Path $RuntimeDir ".venv")
    }
    if ($LASTEXITCODE -ne 0) { throw "创建虚拟环境失败。" }
}

if (-not (Test-Path $DepsMarker)) {
    Write-Host "[Hermes] 首次运行：正在安装依赖（可能需要 1-3 分钟，请耐心等待）..." -ForegroundColor Cyan
    & $VenvPython -m pip install -q -r (Join-Path $Root "requirements.txt")
    if ($LASTEXITCODE -ne 0) { throw "安装知识服务依赖失败。" }
    & $VenvPython -m pip install -q -e "$RuntimeDir[web]"
    if ($LASTEXITCODE -ne 0) { throw "安装网页工作台依赖失败。" }
    Set-Content -Path $DepsMarker -Value (Get-Date -Format o)
}

# 3. Bootstrap .env -> config.yaml (never prints secrets)
& $VenvPython (Join-Path $Root "scripts\bootstrap_env.py")
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$envMap = Load-DotEnv (Join-Path $Root ".env")
$ApiToken = $envMap["HERMES_API_TOKEN"]
$ApiKey = if ($envMap["DEEPSEEK_API_KEY"]) { $envMap["DEEPSEEK_API_KEY"] } else { $envMap["OPENAI_API_KEY"] }
$BaseUrl = if ($envMap["LLM_BASE_URL"]) { $envMap["LLM_BASE_URL"].TrimEnd("/") } else { "https://api.deepseek.com" }
if (-not $BaseUrl.EndsWith("/v1")) { $OpenAiBaseUrl = "$BaseUrl/v1" } else { $OpenAiBaseUrl = $BaseUrl }
$VaultRel = if ($envMap["VAULT_PATH"]) { $envMap["VAULT_PATH"] } else { "./data/vault" }
$VaultAbs = if ([System.IO.Path]::IsPathRooted($VaultRel)) { $VaultRel } else { Join-Path $Root $VaultRel }

$env:PYTHONPATH = $Root
$env:HERMES_API_TOKEN = $ApiToken
$env:HERMES_KNOWLEDGE_URL = "http://127.0.0.1:8000"
$env:HERMES_KNOWLEDGE_TOKEN = $ApiToken
$env:HERMES_KNOWLEDGE_VAULT = $VaultAbs
$env:HERMES_KNOWLEDGE_DB = Join-Path $Root "data\captures.db"
$env:OPENAI_API_KEY = $ApiKey
$env:OPENAI_BASE_URL = $OpenAiBaseUrl

Write-Host "[Hermes] 正在启动知识服务 (127.0.0.1:8000)..." -ForegroundColor Cyan
$svcProc = Start-Process -FilePath $VenvPython `
    -ArgumentList @("-m", "uvicorn", "api.main:create_app", "--factory", "--host", "127.0.0.1", "--port", "8000") `
    -WorkingDirectory $Root `
    -NoNewWindow -PassThru

$ready = $false
for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep -Milliseconds 500
    try {
        $resp = Invoke-WebRequest -Uri "http://127.0.0.1:8000/api/health" -Headers @{ Authorization = "Bearer $ApiToken" } -TimeoutSec 2 -UseBasicParsing
        if ($resp.StatusCode -eq 200) { $ready = $true; break }
    } catch {}
}

if (-not $ready) {
    if (-not $svcProc.HasExited) { Stop-Process -Id $svcProc.Id -Force -ErrorAction SilentlyContinue }
    throw "知识服务启动超时，请检查端口 8000 是否被占用或查看上方报错。"
}

Write-Host "[Hermes] 知识服务已就绪！正在启动网页工作台 (http://127.0.0.1:9119)..." -ForegroundColor Green
Write-Host "[Hermes] 按 Ctrl + C 可同时关闭全部服务。" -ForegroundColor DarkGray

try {
    Set-Location $RuntimeDir
    & $VenvPython -u -m hermes_cli.main dashboard
} finally {
    Write-Host "`n[Hermes] 正在关闭后台知识服务..." -ForegroundColor Yellow
    if ($svcProc -and -not $svcProc.HasExited) {
        Stop-Process -Id $svcProc.Id -Force -ErrorAction SilentlyContinue
    }
}

param(
    [switch]$SkipDjango,
    [switch]$SkipFastAPI,
    [switch]$SkipCelery,
    [switch]$SkipBeat,
    [switch]$SkipRedis
)

$Root = Split-Path -Parent $PSScriptRoot
$Backend = Join-Path $Root "backend"
$Realtime = Join-Path $Root "realtime"
$Venv = Join-Path $Backend ".venv\Scripts\python.exe"

if (-not (Test-Path $Venv)) {
    Write-Host "Virtual environment not found at $Venv" -ForegroundColor Red
    exit 1
}

if (-not $SkipRedis) {
    if (Get-Command redis-server -ErrorAction SilentlyContinue) {
        Start-Process redis-server -WindowStyle Hidden
        Write-Host "Redis started (native)." -ForegroundColor Green
    } else {
        Write-Host "No redis-server on PATH. Ensure Redis is running (docker: docker run -d -p 6379:6379 redis:7-alpine)." -ForegroundColor Yellow
    }
}

if (-not $SkipCelery) {
    Start-Process $Venv -ArgumentList "-m", "celery", "-A", "config", "worker", "--loglevel=info", "--pool=solo" -WorkingDirectory $Backend -WindowStyle Hidden
    Write-Host "Celery worker started." -ForegroundColor Green
}

if (-not $SkipBeat) {
    Start-Process $Venv -ArgumentList "-m", "celery", "-A", "config", "beat", "--loglevel=info" -WorkingDirectory $Backend -WindowStyle Hidden
    Write-Host "Celery beat started." -ForegroundColor Green
}

if (-not $SkipDjango) {
    Start-Process $Venv -ArgumentList "manage.py", "runserver", "127.0.0.1:8000", "--noreload" -WorkingDirectory $Backend -WindowStyle Hidden
    Write-Host "Django running at http://127.0.0.1:8000" -ForegroundColor Green
}

if (-not $SkipFastAPI) {
    Start-Process $Venv -ArgumentList "-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", "8001", "--reload" -WorkingDirectory $Realtime -WindowStyle Hidden
    Write-Host "FastAPI running at http://127.0.0.1:8001" -ForegroundColor Green
}

Write-Host "All services launched. Logs in their windows." -ForegroundColor Cyan
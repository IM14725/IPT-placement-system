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

function Test-RedisPing {
    param([int]$TimeoutSec = 5)
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    do {
        $client = New-Object System.Net.Sockets.TcpClient
        try {
            $client.ReceiveTimeout = 1000
            $client.SendTimeout = 1000
            $connect = $client.ConnectAsync("127.0.0.1", 6379)
            if ($connect.Wait(1000) -and $client.Connected) {
                $stream = $client.GetStream()
                $bytes = [System.Text.Encoding]::ASCII.GetBytes("PING`r`n")
                $stream.Write($bytes, 0, $bytes.Length)
                $buffer = New-Object byte[] 16
                $read = $stream.Read($buffer, 0, $buffer.Length)
                if ([System.Text.Encoding]::ASCII.GetString($buffer, 0, $read) -like "+PONG*") {
                    return $true
                }
            }
        } catch {
        } finally {
            $client.Close()
        }
        Start-Sleep -Milliseconds 500
    } while ((Get-Date) -lt $deadline)
    return $false
}

function Get-RedisListener {
    # Prefer the 127.0.0.1 bind (the more specific one wins for 127.0.0.1 clients).
    $listeners = Get-NetTCPConnection -LocalPort 6379 -State Listen -ErrorAction SilentlyContinue
    if (-not $listeners) { return $null }
    $pick = $listeners | Where-Object { $_.LocalAddress -eq "127.0.0.1" } | Select-Object -First 1
    if (-not $pick) { $pick = $listeners | Select-Object -First 1 }
    return Get-Process -Id $pick.OwningProcess -ErrorAction SilentlyContinue
}

if (-not $SkipRedis) {
    # Start native Redis only if nothing is already listening (native or Docker).
    if (-not (Get-RedisListener) -and (Get-Command redis-server -ErrorAction SilentlyContinue)) {
        Start-Process redis-server -WindowStyle Hidden
    }

    if (Test-RedisPing) {
        $proc = Get-RedisListener
        $name = if ($proc) { $proc.ProcessName } else { "unknown" }
        if ($name -like "redis-server*") {
            Write-Host "Redis connected (native redis-server) on 127.0.0.1:6379." -ForegroundColor Green
        } elseif ($name -match "^(com\.docker|docker|wslrelay|vpnkit)") {
            $container = $null
            if (Get-Command docker -ErrorAction SilentlyContinue) {
                $container = (docker ps --filter "publish=6379" --format "{{.Names}}" 2>$null | Select-Object -First 1)
            }
            if ($container) {
                Write-Host "Redis connected (Docker: $container) on 127.0.0.1:6379." -ForegroundColor Green
            } elseif ($name -eq "wslrelay") {
                # Port is forwarded from WSL but no container owns it: Redis installed natively inside WSL.
                Write-Host "Redis connected (native, via WSL) on 127.0.0.1:6379." -ForegroundColor Green
            } else {
                Write-Host "Redis connected (Docker) on 127.0.0.1:6379." -ForegroundColor Green
            }
        } else {
            Write-Host "Redis connected (process: $name) on 127.0.0.1:6379." -ForegroundColor Green
        }
    } else {
        Write-Host "Redis NOT reachable on 127.0.0.1:6379. Start native redis-server, or: docker start ipt-redis (or docker run -d --name ipt-redis -p 6379:6379 redis:7-alpine)." -ForegroundColor Red
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
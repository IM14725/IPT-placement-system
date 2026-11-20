<#
Tunes a single-server PostgreSQL (Windows, ~8 GB RAM shared with Django, Redis, Celery and FastAPI)
using ALTER SYSTEM, which writes postgresql.auto.conf. Nothing in postgresql.conf is edited by hand.

Usage (as a Postgres superuser; adjust sizes for other RAM with -RamGb):
  scripts\postgres_tune.ps1 -SuperUser postgres            # prompts for the password via PGPASSWORD
  scripts\postgres_tune.ps1 -RamGb 16 -WhatIf              # just print the statements

Some settings need a service restart (shared_buffers, shared_preload_libraries, max_connections);
the script says which. Rationale for each value: docs\postgres-tuning.md.
#>
param(
    [string]$SuperUser = "postgres",
    [string]$DbHost = "127.0.0.1",
    [int]$Port = 5432,
    [int]$RamGb = 8,
    [string]$Database = "ipt_marketplace",
    [switch]$WhatIf
)

$ramMb = $RamGb * 1024
# Postgres shares the box with the app tier, so it gets roughly 60% of RAM in total.
$sharedBuffers = [int]($ramMb * 0.25)          # 25% of RAM
$effectiveCache = [int]($ramMb * 0.60)         # planner hint: what the OS cache + shared_buffers can hold
$maintenanceMem = [Math]::Min(1024, [int]($ramMb * 0.06))
$workMem = 16                                  # MB per sort/hash node; with max_connections=100 worst case stays sane
$walBuffers = 16

$settings = [ordered]@{
    "max_connections"                      = "100"            # restart. Django pools + Celery + realtime must fit (see docs).
    "shared_buffers"                       = "${sharedBuffers}MB"   # restart
    "effective_cache_size"                 = "${effectiveCache}MB"
    "maintenance_work_mem"                 = "${maintenanceMem}MB"
    "work_mem"                             = "${workMem}MB"
    "wal_buffers"                          = "${walBuffers}MB"      # restart
    "checkpoint_completion_target"         = "0.9"
    "max_wal_size"                         = "4GB"
    "min_wal_size"                         = "1GB"
    "random_page_cost"                     = "1.1"            # SSD
    "effective_io_concurrency"             = "200"            # SSD (not honoured on Windows, harmless)
    "default_statistics_target"            = "200"
    "idle_in_transaction_session_timeout"  = "60s"            # a stuck request must not hold row locks (seat booking!)
    "lock_timeout"                         = "15s"
    # Slow-query visibility
    "shared_preload_libraries"             = "pg_stat_statements"   # restart
    "pg_stat_statements.max"               = "5000"
    "pg_stat_statements.track"             = "top"
    "log_min_duration_statement"           = "500ms"
    "log_lock_waits"                       = "on"
    "deadlock_timeout"                     = "1s"
    "log_temp_files"                       = "0"
    "log_autovacuum_min_duration"          = "1s"
    # Autovacuum: busy small tables (applications, notifications, sessions) need frequent, cheap runs
    "autovacuum_vacuum_scale_factor"       = "0.05"
    "autovacuum_analyze_scale_factor"      = "0.02"
    "autovacuum_vacuum_cost_limit"         = "1000"
}

$restartNeeded = @("max_connections", "shared_buffers", "wal_buffers", "shared_preload_libraries")
$psql = (Get-Command psql -ErrorAction SilentlyContinue).Source
if (-not $psql -and -not $WhatIf) { throw "psql not found on PATH. Add PostgreSQL's bin folder to PATH." }

function Invoke-Sql([string]$sql, [string]$db = "postgres") {
    if ($WhatIf) { Write-Host "  $sql"; return }
    & $psql -h $DbHost -p $Port -U $SuperUser -d $db -v ON_ERROR_STOP=1 -q -c $sql
    if ($LASTEXITCODE -ne 0) { throw "psql failed for: $sql" }
}

foreach ($name in $settings.Keys) {
    $value = $settings[$name]
    $flag = if ($restartNeeded -contains $name) { "  (needs restart)" } else { "" }
    Write-Host "$name = $value$flag"
    Invoke-Sql "ALTER SYSTEM SET $name = '$value';"
}
Invoke-Sql "SELECT pg_reload_conf();"

# pg_stat_statements needs the library preloaded (restart) AND the extension created in the database.
Write-Host "Creating pg_stat_statements extension in $Database (works after the restart)..."
if (-not $WhatIf) {
    try { Invoke-Sql "CREATE EXTENSION IF NOT EXISTS pg_stat_statements;" $Database }
    catch { Write-Warning "Run this again after restarting PostgreSQL: $_" }
}

Write-Host ""
Write-Host "Done. Restart the PostgreSQL service once to apply: $($restartNeeded -join ', ')."
Write-Host "  Restart-Service (Get-Service postgresql*)   # from an elevated PowerShell"

param(
    [string]$DbUser = "ipt",
    [string]$DbName = "ipt_marketplace",
    [string]$DbPassword = $env:DB_PASSWORD
)

if (-not $DbPassword) {
    Write-Host "DB_PASSWORD env var not set. Using default dev password 'ipt_dev_password'." -ForegroundColor Yellow
    $DbPassword = "ipt_dev_password"
}

$env:PGPASSWORD = $env:PG_SUPERUSER_PASSWORD

$sql = @'
DO $$
BEGIN
   IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '__DBUSER__') THEN
      CREATE ROLE __DBUSER__ LOGIN PASSWORD '__DBPASSWORD__';
   END IF;
END
$$;
'@
$sql = $sql.Replace('__DBUSER__', $DbUser).Replace('__DBPASSWORD__', $DbPassword)

Write-Host "Creating role '$DbUser'..." -ForegroundColor Cyan
psql -U $env:PG_USER -h localhost -d postgres -w -v ON_ERROR_STOP=1 -c $sql
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "Creating database '$DbName'..." -ForegroundColor Cyan
psql -U $env:PG_USER -h localhost -d postgres -w -v ON_ERROR_STOP=1 -c "SELECT 1 FROM pg_database WHERE datname = '$DbName'" | Out-Null
$exists = psql -U $env:PG_USER -h localhost -d postgres -w -t -c "SELECT 1 FROM pg_database WHERE datname = '$DbName'"
if (-not $exists.Trim()) {
    psql -U $env:PG_USER -h localhost -d postgres -w -v ON_ERROR_STOP=1 -c "CREATE DATABASE $DbName OWNER $DbUser ENCODING 'UTF8'"
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    Write-Host "Database '$DbName' created." -ForegroundColor Green
} else {
    Write-Host "Database '$DbName' already exists." -ForegroundColor Green
}

Write-Host "Done." -ForegroundColor Green
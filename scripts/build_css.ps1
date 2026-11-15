<#
Compile Tailwind CSS (replaces the cdn.tailwindcss.com runtime script, which blocked page rendering
for seconds on slow networks). Output: backend/static/css/tailwind.css (committed, so the app runs
without this step). Re-run after adding/changing Tailwind classes in templates, JS or Python.

  scripts\build_css.ps1            one-off minified build
  scripts\build_css.ps1 -Watch     rebuild on every change (dev.ps1 starts this automatically)

The standalone Tailwind v3.4.17 CLI (no Node needed) is downloaded to tools\ on first use.
#>
param([switch]$Watch)

$Root = Split-Path -Parent $PSScriptRoot
$Backend = Join-Path $Root "backend"
$Tools = Join-Path $Root "tools"
$Exe = Join-Path $Tools "tailwindcss.exe"
$Version = "v3.4.17"
$Url = "https://github.com/tailwindlabs/tailwindcss/releases/download/$Version/tailwindcss-windows-x64.exe"

if (-not (Test-Path $Exe) -or (Get-Item $Exe).Length -lt 30000000) {
    New-Item -ItemType Directory -Force $Tools | Out-Null
    Write-Host "Downloading Tailwind CLI $Version ..." -ForegroundColor Cyan
    Invoke-WebRequest -Uri $Url -OutFile $Exe
}

Push-Location $Backend
try {
    $cliArgs = @("-c", "tailwind.config.js", "-i", "tailwind.input.css", "-o", "static/css/tailwind.css", "--minify")
    if ($Watch) { $cliArgs += "--watch" }
    & $Exe @args
} finally {
    Pop-Location
}

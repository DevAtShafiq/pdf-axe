# Build StudentFolderMaker (folder layout — avoids multi-GB %TEMP% _MEI* extraction)
# opencv-python + numpy removed from build — saves ~146 MB (264 MB → ~118 MB dist)
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

Write-Host "Installing build dependencies..." -ForegroundColor Cyan
python -m pip install -r requirements-build.txt --quiet

Write-Host "Generating icon.ico..." -ForegroundColor Cyan
python build_icon.py

if (-not (Test-Path "icon.ico")) {
    throw "icon.ico was not created."
}

$appName = "StudentFolderMaker"
$outDir = Join-Path $Root "dist\$appName"
$outExe = Join-Path $outDir "$appName.exe"
$specPath = Join-Path $Root "StudentFolderMaker.spec"
$specBackup = $null

$exeLocked = @(Get-Process -Name "StudentFolderMaker*" -ErrorAction SilentlyContinue).Count -gt 0
if ($exeLocked -and (Test-Path $outDir)) {
    $appName = "StudentFolderMaker_new"
    $outDir = Join-Path $Root "dist\$appName"
    $outExe = Join-Path $outDir "$appName.exe"
    Write-Host "Existing EXE is in use - building to dist\$appName instead." -ForegroundColor Yellow
    Write-Host "Close the running app, then replace dist\StudentFolderMaker with the new folder." -ForegroundColor Yellow
    $specText = Get-Content -LiteralPath $specPath -Raw -Encoding UTF8
    $specBackup = $specText
    $patched = $specText -replace 'name="StudentFolderMaker"', ('name="{0}"' -f $appName)
    Set-Content -LiteralPath $specPath -Value $patched -Encoding UTF8 -NoNewline
}

try {
    Write-Host "Running PyInstaller (onedir via StudentFolderMaker.spec)..." -ForegroundColor Cyan
    python -m PyInstaller --noconfirm StudentFolderMaker.spec
} finally {
    if ($null -ne $specBackup) {
        Set-Content -LiteralPath $specPath -Value $specBackup -Encoding UTF8 -NoNewline
    }
}

Write-Host ""
if (Test-Path $outExe) {
    Write-Host "Done. Run from:" -ForegroundColor Green
    Write-Host "  $outExe" -ForegroundColor Green
    Write-Host ""
    Write-Host 'Copy the ENTIRE folder dist\StudentFolderMaker\ (includes _internal).' -ForegroundColor Yellow
    Write-Host 'Temp files go to <that folder>\_sfm_temp (not C:\Users\...\Temp\_MEI*).' -ForegroundColor Yellow
} else {
    throw "Build failed: $outExe not found."
}

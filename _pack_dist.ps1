$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

$src = "dist\StudentFolderMaker"
$dst = "dist\StudentFolderMaker.zip"

if (-not (Test-Path $src)) { throw "Build folder not found: $src" }

if (Test-Path $dst) {
    $stamp = Get-Date -Format "yyyyMMdd_HHmmss"
    Rename-Item -LiteralPath $dst -NewName ("StudentFolderMaker_old_{0}.zip" -f $stamp)
}

Write-Host "Zipping $src ..." -ForegroundColor Cyan
Compress-Archive -Path (Join-Path $src "*") -DestinationPath $dst -CompressionLevel Optimal

$zip = Get-Item -LiteralPath $dst
$mb = [math]::Round($zip.Length / 1MB, 1)
Write-Host ("Created {0} ({1} MB)" -f $zip.FullName, $mb) -ForegroundColor Green

# Verify the critical runtime DLLs are inside the archive.
Add-Type -AssemblyName System.IO.Compression.FileSystem
$archive = [System.IO.Compression.ZipFile]::OpenRead($zip.FullName)
$need = @(
    "_internal/python311.dll",
    "_internal/VCRUNTIME140.dll",
    "_internal/VCRUNTIME140_1.dll",
    "_internal/MSVCP140.dll",
    "_internal/ucrtbase.dll",
    "StudentFolderMaker.exe"
)
$names = $archive.Entries | ForEach-Object { $_.FullName.Replace('\','/').ToLower() }
foreach ($n in $need) {
    $hit = $names | Where-Object { $_ -eq $n.ToLower() }
    if ($hit) { Write-Host ("  OK   {0}" -f $n) -ForegroundColor Green }
    else      { Write-Host ("  MISS {0}" -f $n) -ForegroundColor Red }
}
Write-Host ("Total entries in zip: {0}" -f $archive.Entries.Count)
$archive.Dispose()

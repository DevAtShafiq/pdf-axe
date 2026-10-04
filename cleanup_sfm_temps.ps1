# cleanup_sfm_temps.ps1
# One-click cleanup for StudentFolderMaker / PyInstaller temp leftovers on C drive.
# Run this ONCE to reclaim space from all previously accumulated stale temp files.
# After rebuilding the exe with the updated .spec, new runs will not accumulate anymore.

$ErrorActionPreference = "SilentlyContinue"
$tempDir = [System.IO.Path]::GetTempPath()

Write-Host ""
Write-Host "=== StudentFolderMaker Temp Cleanup ===" -ForegroundColor Cyan
Write-Host "Scanning: $tempDir"
Write-Host ""

$totalBytes = 0
$removedCount = 0

# --- 1. PyInstaller _MEI* extraction folders ---
Write-Host "Checking for stale _MEI* PyInstaller folders..." -ForegroundColor Yellow
$meiFolders = Get-ChildItem -Path $tempDir -Directory -Filter "_MEI*" -ErrorAction SilentlyContinue
foreach ($folder in $meiFolders) {
    try {
        $size = (Get-ChildItem $folder.FullName -Recurse -File -ErrorAction SilentlyContinue |
                 Measure-Object -Property Length -Sum).Sum
        Write-Host "  Removing: $($folder.Name)  ($([math]::Round($size/1MB,1)) MB)" -ForegroundColor Gray
        Remove-Item $folder.FullName -Recurse -Force -ErrorAction SilentlyContinue
        $totalBytes += $size
        $removedCount++
    } catch {
        Write-Host "  Skipped (in use): $($folder.Name)" -ForegroundColor DarkGray
    }
}
if ($meiFolders.Count -eq 0) {
    Write-Host "  None found." -ForegroundColor Green
}

# --- 2. App-created sfm_* temp files and folders ---
Write-Host ""
Write-Host "Checking for stale sfm_* app temp files..." -ForegroundColor Yellow
$sfmItems = Get-ChildItem -Path $tempDir -Filter "sfm_*" -ErrorAction SilentlyContinue
foreach ($item in $sfmItems) {
    try {
        if ($item.PSIsContainer) {
            $size = (Get-ChildItem $item.FullName -Recurse -File -ErrorAction SilentlyContinue |
                     Measure-Object -Property Length -Sum).Sum
        } else {
            $size = $item.Length
        }
        Write-Host "  Removing: $($item.Name)  ($([math]::Round($size/1KB,1)) KB)" -ForegroundColor Gray
        Remove-Item $item.FullName -Recurse -Force -ErrorAction SilentlyContinue
        $totalBytes += $size
        $removedCount++
    } catch {
        Write-Host "  Skipped (in use): $($item.Name)" -ForegroundColor DarkGray
    }
}
if ($sfmItems.Count -eq 0) {
    Write-Host "  None found." -ForegroundColor Green
}

# --- Summary ---
Write-Host ""
Write-Host "=== Done ===" -ForegroundColor Cyan
if ($removedCount -gt 0) {
    Write-Host "Removed $removedCount item(s), freed $([math]::Round($totalBytes/1MB,1)) MB." -ForegroundColor Green
} else {
    Write-Host "Nothing to remove -- temp folder is already clean." -ForegroundColor Green
}
Write-Host ""
Write-Host 'NOTE: Rebuild with build_exe.ps1 (onedir). Use dist\StudentFolderMaker\ — not an old one-file exe.' -ForegroundColor Yellow
Write-Host ""
Read-Host 'Press Enter to close'

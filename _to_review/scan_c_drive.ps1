# C: Drive Space Analyzer
# Run this in PowerShell as Administrator for best results
# Right-click the file -> "Run with PowerShell"

Write-Host "`n===== C: DRIVE SPACE REPORT =====" -ForegroundColor Cyan

# Overall disk usage
$disk = Get-PSDrive C
$totalGB = [math]::Round(($disk.Used + $disk.Free) / 1GB, 2)
$usedGB  = [math]::Round($disk.Used / 1GB, 2)
$freeGB  = [math]::Round($disk.Free / 1GB, 2)

Write-Host "`nTotal: $totalGB GB   Used: $usedGB GB   Free: $freeGB GB" -ForegroundColor Yellow

# Top-level folder sizes (excluding protected system folders)
Write-Host "`n--- Top folders on C:\ ---" -ForegroundColor Cyan
$folders = @("C:\Users", "C:\Program Files", "C:\Program Files (x86)", "C:\ProgramData", "C:\Windows")

foreach ($folder in $folders) {
    if (Test-Path $folder) {
        try {
            $size = (Get-ChildItem $folder -Recurse -ErrorAction SilentlyContinue | Measure-Object -Property Length -Sum).Sum
            $sizeGB = [math]::Round($size / 1GB, 2)
            Write-Host ("  {0,-30} {1,8} GB" -f $folder, $sizeGB)
        } catch {
            Write-Host "  $folder  (could not read)" -ForegroundColor DarkGray
        }
    }
}

# User-specific large folders
Write-Host "`n--- Your user folders (C:\Users\$env:USERNAME) ---" -ForegroundColor Cyan
$userFolders = @("Desktop", "Documents", "Downloads", "Pictures", "Videos", "Music", "AppData\Local\Temp")

foreach ($sub in $userFolders) {
    $path = "C:\Users\$env:USERNAME\$sub"
    if (Test-Path $path) {
        try {
            $size = (Get-ChildItem $path -Recurse -ErrorAction SilentlyContinue | Measure-Object -Property Length -Sum).Sum
            $sizeGB = [math]::Round($size / 1GB, 2)
            Write-Host ("  {0,-30} {1,8} GB" -f $sub, $sizeGB)
        } catch {
            Write-Host "  $sub  (could not read)" -ForegroundColor DarkGray
        }
    }
}

# Temp folder size
$tempPath = $env:TEMP
$tempSize = (Get-ChildItem $tempPath -Recurse -ErrorAction SilentlyContinue | Measure-Object -Property Length -Sum).Sum
$tempSizeGB = [math]::Round($tempSize / 1GB, 2)
Write-Host "`n--- Windows Temp folder ($tempPath) ---" -ForegroundColor Cyan
Write-Host "  Size: $tempSizeGB GB  (safe to delete contents)" -ForegroundColor Green

# Recycle Bin
Write-Host "`n--- Recycle Bin ---" -ForegroundColor Cyan
try {
    $shell = New-Object -ComObject Shell.Application
    $recycleBin = $shell.Namespace(0xA)
    $rbSize = ($recycleBin.Items() | Measure-Object -Property Size -Sum).Sum
    $rbSizeGB = [math]::Round($rbSize / 1GB, 2)
    Write-Host "  Recycle Bin contains: $rbSizeGB GB"
} catch {
    Write-Host "  Could not read Recycle Bin" -ForegroundColor DarkGray
}

Write-Host "`n===== QUICK CLEANUP TIPS =====" -ForegroundColor Cyan
Write-Host "  1. Run Disk Cleanup: Press Win+R -> type 'cleanmgr' -> OK"
Write-Host "  2. Clear Temp:       Press Win+R -> type '%temp%' -> select all -> delete"
Write-Host "  3. Move large files in Downloads/Videos/Documents to D: drive"
Write-Host "  4. Uninstall unused apps: Settings -> Apps -> sort by Size"
Write-Host "  5. Check for big files: Win+R -> type 'cleanmgr /sageset:1' for system cleanup"

Write-Host "`nPress any key to exit..."
$null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")

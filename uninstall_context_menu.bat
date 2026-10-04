@echo off
:: ============================================================
::  Document Auto-Renaming — Right-Click UNINSTALLER
::  Double-click as Administrator to remove the menu entry.
:: ============================================================

net session >nul 2>&1
if %errorLevel% neq 0 (
    echo.
    echo  [!] Needs Administrator rights.
    echo      Right-click this file and choose "Run as administrator".
    echo.
    pause
    exit /b 1
)

echo.
echo  Removing "Rename File (OCR)" from right-click menu ...
echo.

:: Remove from * (all files)
reg delete "HKEY_CLASSES_ROOT\*\shell\OCRRename" /f >nul 2>&1
echo  [OK] Removed from * (all files)

:: Remove from specific ProgIDs
for %%E in (.pdf .png .jpg .jpeg .tiff .tif .bmp .webp) do (
    for /f "tokens=2*" %%a in ('reg query "HKEY_CLASSES_ROOT\%%E" /ve 2^>nul') do (
        reg delete "HKEY_CLASSES_ROOT\%%b\shell\OCRRename" /f >nul 2>&1
    )
    reg delete "HKEY_CLASSES_ROOT\%%Efile\shell\OCRRename" /f >nul 2>&1
    echo  [OK] Cleaned %%E
)

echo.
echo  Done. The right-click option has been removed.
echo.
pause

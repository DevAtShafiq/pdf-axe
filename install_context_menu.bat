@echo off
:: ============================================================
::  Document Auto-Renaming — Windows Right-Click Installer
::  Double-click this file (as Administrator) to install.
::  It adds "Rename File (OCR)" to the right-click menu for
::  PDF, PNG, JPG, JPEG, TIFF, BMP and WEBP files.
:: ============================================================

:: Check for admin rights
net session >nul 2>&1
if %errorLevel% neq 0 (
    echo.
    echo  [!] This installer needs Administrator rights.
    echo      Right-click this file and choose "Run as administrator".
    echo.
    pause
    exit /b 1
)

:: Detect python location
for /f "delims=" %%i in ('where python 2^>nul') do set PYTHON=%%i
if "%PYTHON%"=="" (
    echo.
    echo  [!] Python not found in PATH.
    echo      Please install Python and make sure it is on your PATH.
    echo.
    pause
    exit /b 1
)

:: Path to this folder and the main script
set SCRIPT_DIR=%~dp0
set SCRIPT=%SCRIPT_DIR%doc_renamer.py
set LAUNCHER=%SCRIPT_DIR%_ocr_rename_launcher.bat

echo.
echo  Python   : %PYTHON%
echo  Script   : %SCRIPT%
echo  Launcher : %LAUNCHER%
echo.

:: ------------------------------------------------------------------
:: Write the launcher .bat that the context menu calls.
:: It runs the rename, then pauses so the user sees the result.
:: ------------------------------------------------------------------
(
echo @echo off
echo title OCR Document Renamer
echo color 0A
echo echo.
echo echo  ================================================
echo echo   Rename File ^(OCR^)
echo echo  ================================================
echo echo.
echo set PYTHONIOENCODING=utf-8
echo "%PYTHON%" "%SCRIPT%" --file "%%~1"
echo echo.
echo echo  Press any key to close this window ...
echo pause ^>nul
) > "%LAUNCHER%"

echo  [OK] Launcher created.

:: ------------------------------------------------------------------
:: Register for each file type under HKEY_CLASSES_ROOT
:: ------------------------------------------------------------------

set MENU_LABEL=Rename File (OCR)
set CMD="%LAUNCHER%" "%%1"

:: Helper: register one extension
:: Usage:  call :register_ext .pdf
goto :skip_fn

:register_ext
set EXT=%~1
:: Find or create the ProgID for this extension
for /f "tokens=2*" %%a in ('reg query "HKEY_CLASSES_ROOT\%EXT%" /ve 2^>nul') do set PROGID=%%b
if "%PROGID%"=="" set PROGID=%EXT%file

:: Add menu entry under the ProgID
reg add "HKEY_CLASSES_ROOT\%PROGID%\shell\OCRRename"         /ve /d "%MENU_LABEL%" /f >nul
reg add "HKEY_CLASSES_ROOT\%PROGID%\shell\OCRRename"         /v "Icon" /d "shell32.dll,71" /f >nul
reg add "HKEY_CLASSES_ROOT\%PROGID%\shell\OCRRename\command" /ve /d "%CMD%" /f >nul

:: Also add under * (all files) as a belt-and-suspenders fallback
reg add "HKEY_CLASSES_ROOT\*\shell\OCRRename"         /ve /d "%MENU_LABEL%" /f >nul
reg add "HKEY_CLASSES_ROOT\*\shell\OCRRename"         /v "Icon" /d "shell32.dll,71" /f >nul
reg add "HKEY_CLASSES_ROOT\*\shell\OCRRename\command" /ve /d "%CMD%" /f >nul

echo  [OK] Registered for %EXT%
goto :eof

:skip_fn

call :register_ext .pdf
call :register_ext .png
call :register_ext .jpg
call :register_ext .jpeg
call :register_ext .tiff
call :register_ext .tif
call :register_ext .bmp
call :register_ext .webp

echo.
echo  =====================================================
echo   Installation complete!
echo  =====================================================
echo.
echo   Right-click any PDF or image file in Windows Explorer
echo   and choose:
echo.
echo       "Rename File (OCR)"
echo.
echo   The file will be automatically renamed based on its
echo   document type detected via OCR.
echo.
echo   To uninstall, run:  uninstall_context_menu.bat
echo.
pause

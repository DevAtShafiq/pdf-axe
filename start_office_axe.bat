@echo off
title Office Axe
cd /d "%~dp0"
set PYTHONUTF8=1

rem --- The desktop app -----------------------------------------------------
echo Starting Office Axe...
python main_webview.py %*

if errorlevel 1 (
    echo.
    echo ERROR: Office Axe exited with an error. See the output above.
    pause
)

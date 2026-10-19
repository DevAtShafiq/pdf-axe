@echo off
title Office Axe
cd /d "%~dp0"
set PYTHONUTF8=1

rem --- Account / cloud server (sign-in, Office drive) -----------------------
rem Starts only if nothing is answering on port 8000 yet.
rem SFM_FREE_PLAN=1 unlocks the paid features while Stripe isn't set up;
rem remove that line once real Stripe keys are in server\.env.
python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)" >nul 2>&1
if errorlevel 1 (
    echo Starting the Office Axe account server...
    set SFM_FREE_PLAN=1
    start "Office Axe server" /min python -m uvicorn server.app:app --host 127.0.0.1 --port 8000
    timeout /t 4 /nobreak >nul
) else (
    echo Account server already running.
)

rem --- The desktop app -----------------------------------------------------
set SFM_SERVER_URL=http://127.0.0.1:8000
echo Starting Office Axe...
python main_webview.py %*

if errorlevel 1 (
    echo.
    echo ERROR: Office Axe exited with an error. See the output above.
    pause
)

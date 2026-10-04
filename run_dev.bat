@echo off
title StudentFolderMaker — Dev Mode
cd /d "%~dp0"

echo.
echo ============================================================
echo  StudentFolderMaker v2 — Dev Mode (no build required)
echo  Press Ctrl+C to stop.
echo ============================================================
echo.

:: Optional: pass --debug to enable PyWebView DevTools (F12 in the window)
python main_webview.py %*

if errorlevel 1 (
    echo.
    echo ERROR: App exited with an error. See output above.
    pause
)

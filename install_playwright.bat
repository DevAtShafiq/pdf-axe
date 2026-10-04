@echo off
title Installing Playwright for Apostille Scraping
cd /d "%~dp0"
echo.
echo ============================================================
echo  Installing Playwright (headless browser for apostille.mygov.bd)
echo ============================================================
echo.
echo [1/2] Installing playwright Python package...
python -m pip install playwright
if errorlevel 1 ( echo ERROR: pip install failed & pause & exit /b 1 )
echo.
echo [2/2] Downloading Chromium browser...
python -m playwright install chromium
if errorlevel 1 ( echo ERROR: playwright install failed & pause & exit /b 1 )
echo.
echo ============================================================
echo  DONE — Apostille scraping is now fully active.
echo  You can close this window.
echo ============================================================
echo.
pause

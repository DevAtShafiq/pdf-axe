@echo off
title Close App and Rebuild
cd /d "%~dp0"

echo Closing StudentFolderMaker if running...
taskkill /f /im StudentFolderMaker.exe >nul 2>&1
timeout /t 2 /nobreak >nul

echo Unlocking dist folder (taking ownership + granting full control)...
if exist "dist\StudentFolderMaker" (
    takeown /f "dist\StudentFolderMaker" /r /d y >nul 2>&1
    icacls "dist\StudentFolderMaker" /grant "%USERNAME%:F" /t /q >nul 2>&1
    timeout /t 1 /nobreak >nul
)

echo Starting rebuild...
call rebuild.bat

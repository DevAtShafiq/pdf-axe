@echo off
rem Choose the website admin password and save it (as a hash only) to Secret
rem Manager, project officeaxe-8a44f. The password is never shown or stored.
title Office Axe - website admin password
setlocal
cd /d "%~dp0"
set FB="%APPDATA%\npm\firebase.cmd"
:again
cls
echo.
echo  Office Axe - make up a password for the website admin page
echo  -----------------------------------------------------------
echo  This is a NEW password that you choose now (you don't have it yet).
echo  You will use it to log in at  https://officeaxe-8a44f.web.app/admin
echo  with the user name  admin
echo.
echo  - at least 10 characters, e.g. a short sentence
echo  - nothing appears on screen while you type: that is normal
echo  - press Enter after typing it, then type it again and press Enter
echo.
python ..\manage.py hash-password > "%TEMP%\oa_admin_hash.txt"
if errorlevel 1 (
  del "%TEMP%\oa_admin_hash.txt" >nul 2>&1
  echo.
  choice /c YN /m " Try again"
  if errorlevel 2 exit /b 1
  goto again
)
echo.
echo  Saving it to Google Secret Manager...
call %FB% functions:secrets:set OFFICEAXE_ADMIN_PASSWORD_HASH --data-file "%TEMP%\oa_admin_hash.txt" --project officeaxe-8a44f --force
set RC=%ERRORLEVEL%
del "%TEMP%\oa_admin_hash.txt" >nul 2>&1
echo.
if "%RC%"=="0" (
  echo  Done - your website admin password is saved.
  echo  Tell Claude "secrets done".
) else (
  echo  Saving failed - the reason is shown above. Run this window again to retry.
)
echo.
pause

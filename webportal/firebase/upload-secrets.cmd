@echo off
rem Upload the Office Axe portal's three secrets to Google Secret Manager
rem (project officeaxe-8a44f). Nothing secret is shown on screen or saved
rem anywhere else; the temporary files are removed at the end.
title Office Axe - upload secrets
setlocal
cd /d "%~dp0"
set FB="%APPDATA%\npm\firebase.cmd"
set PROJECT=officeaxe-8a44f
echo.
echo  Office Axe - upload the portal secrets (project %PROJECT%)
echo  ----------------------------------------------------------
echo  If Firebase asks to enable the Secret Manager API, answer Y.
echo.
if not exist "%USERPROFILE%\OfficeAxeAdmin\signing_key.json" (
  echo  Your signing key %USERPROFILE%\OfficeAxeAdmin\signing_key.json was not found.
  pause
  exit /b 1
)
echo  [1/3] License signing key
call %FB% functions:secrets:set OFFICEAXE_SIGNING_KEY --data-file "%USERPROFILE%\OfficeAxeAdmin\signing_key.json" --project %PROJECT% --force
if errorlevel 1 goto failed
echo.
echo  [2/3] Session secret (random)
python -c "import secrets; print(secrets.token_hex(32))" > "%TEMP%\oa_session.txt"
call %FB% functions:secrets:set OFFICEAXE_SESSION_SECRET --data-file "%TEMP%\oa_session.txt" --project %PROJECT% --force
set RC=%ERRORLEVEL%
del "%TEMP%\oa_session.txt" >nul 2>&1
if not "%RC%"=="0" goto failed
echo.
echo  [3/3] Admin password for the website (type it twice; it is not shown)
python ..\manage.py hash-password > "%TEMP%\oa_admin_hash.txt"
if errorlevel 1 (
  del "%TEMP%\oa_admin_hash.txt" >nul 2>&1
  goto failed
)
call %FB% functions:secrets:set OFFICEAXE_ADMIN_PASSWORD_HASH --data-file "%TEMP%\oa_admin_hash.txt" --project %PROJECT% --force
set RC=%ERRORLEVEL%
del "%TEMP%\oa_admin_hash.txt" >nul 2>&1
if not "%RC%"=="0" goto failed
echo.
echo  All three secrets are uploaded. Tell Claude "secrets done".
echo.
pause
exit /b 0
:failed
echo.
echo  Something went wrong above. Nothing else was changed; you can run this again.
echo.
pause
exit /b 1

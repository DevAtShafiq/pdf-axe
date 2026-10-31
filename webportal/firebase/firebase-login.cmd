@echo off
rem Sign the Firebase CLI in to your Google account (opens your browser).
title Office Axe - Firebase sign-in
echo.
echo  Office Axe - Firebase sign-in
echo  -----------------------------
echo  Answer "n" to the Gemini and usage questions, then sign in in the browser
echo  with the Google account that owns the OFFICEAXE project.
echo.
call "%APPDATA%\npm\firebase.cmd" login
echo.
call "%APPDATA%\npm\firebase.cmd" projects:list
echo.
pause

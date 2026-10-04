@echo off
title StudentFolderMaker v2 — Rebuild
cd /d "%~dp0"

echo.
echo ============================================================
echo  StudentFolderMaker v2 (PyWebView) — Full Rebuild
echo  Entry point : main_webview.py
echo  Quick test  : run_dev.bat   (no build needed)
echo ============================================================
echo.

echo [1/6] Installing / updating runtime dependencies...
python -m pip install -r requirements.txt --quiet
if errorlevel 1 ( echo ERROR: pip install failed & pause & exit /b 1 )

echo [2/6] Installing PyInstaller...
python -m pip install "pyinstaller>=6.0.0" --quiet
if errorlevel 1 ( echo ERROR: PyInstaller install failed & pause & exit /b 1 )

echo [3/6] Generating icon.ico...
python build_icon.py
if errorlevel 1 ( echo WARNING: icon generation failed — using existing icon.ico if present )

echo [4/6] Running PyInstaller...
python -m PyInstaller --noconfirm StudentFolderMaker.spec
if errorlevel 1 ( echo ERROR: PyInstaller failed & pause & exit /b 1 )

echo [5/6] Copying ui\ assets to dist (belt-and-suspenders)...
if exist "ui" (
    xcopy /E /I /Y "ui" "dist\StudentFolderMaker\ui" >nul
    echo   ui\ copied.
)

echo [6/6] Copying user data files to dist...
if exist ".env" (
    copy /Y ".env" "dist\StudentFolderMaker\.env" >nul
    echo   .env copied.
) else (
    echo   WARNING: .env not found — enter your API key in Settings after first launch.
)
for %%f in (sfm_settings.json document_name_templates.txt sfm_rename_templates.json sfm_name_templates.json) do (
    if exist "%%f" (
        copy /Y "%%f" "dist\StudentFolderMaker\%%f" >nul
        echo   %%f copied.
    )
)

echo.
if exist "dist\StudentFolderMaker\StudentFolderMaker.exe" (
    echo  ✅ BUILD SUCCEEDED
    echo  EXE : dist\StudentFolderMaker\StudentFolderMaker.exe
    echo  Distribute the entire dist\StudentFolderMaker\ folder.
) else (
    echo  ❌ BUILD FAILED — EXE not found. Check errors above.
)
echo.
pause

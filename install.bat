@echo off
title ViralClipper Installer
echo.
echo  =============================================
echo   ⚡ ViralClipper — Installer (Windows)
echo  =============================================
echo.

:: Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo  [ERROR] Python not found. Install from https://python.org
    pause & exit /b 1
)
echo  [OK] Python found

:: Check pip
pip --version >nul 2>&1
if errorlevel 1 (
    echo  [ERROR] pip not found.
    pause & exit /b 1
)
echo  [OK] pip found

:: Check ffmpeg
ffmpeg -version >nul 2>&1
if errorlevel 1 (
    echo.
    echo  [WARN] FFmpeg not found!
    echo  Please install it manually:
    echo    1. Go to https://github.com/BtbN/FFmpeg-Builds/releases
    echo    2. Download ffmpeg-master-latest-win64-gpl.zip
    echo    3. Extract and add the bin\ folder to your PATH
    echo.
    echo  Or with winget:  winget install ffmpeg
    echo  Or with choco:   choco install ffmpeg
    echo.
) else (
    echo  [OK] FFmpeg found
)

echo.
echo  Installing Python packages...
pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo  [ERROR] pip install failed. Try:  pip install -r requirements.txt --user
    pause & exit /b 1
)

echo.
:: Copy .env if not exists
if not exist .env (
    copy .env.example .env >nul
    echo  [OK] Created .env  ^(edit it to add your API keys^)
) else (
    echo  [OK] .env already exists
)

:: Create output dir
if not exist output mkdir output

echo.
echo  =============================================
echo   Installation complete!
echo  =============================================
echo.
echo   Start web UI :  python app.py
echo   CLI mode     :  python main.py ^<youtube-url^>
echo.
pause

@echo off
chcp 65001 >nul
title video subtitle remover

set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%"
set "CACHE_DIR=%SCRIPT_DIR%.cache"

:: ========== Pre-flight check ==========
where python >nul 2>nul
if errorlevel 1 (
    echo [Error] Python not found in system PATH.
    echo Please install Python 3.12+ and add it to PATH.
    pause
    exit /b 1
)

:: ========== Environment ==========
:: Centralized bytecode cache (avoids scattered __pycache__ in source dirs)
set "PYTHONPYCACHEPREFIX=%CACHE_DIR%\pycache"
:: Temp files
set "TEMP=%CACHE_DIR%\temp"
set "TMP=%CACHE_DIR%\temp"

:: ========== Create cache directories ==========
if not exist "%CACHE_DIR%" mkdir "%CACHE_DIR%"
if not exist "%CACHE_DIR%\pycache" mkdir "%CACHE_DIR%\pycache"
if not exist "%CACHE_DIR%\temp" mkdir "%CACHE_DIR%\temp"

:: ========== Launch ==========
python "%SCRIPT_DIR%gui.py"
if %errorlevel% neq 0 (
    echo.
    echo [Error] Program exited with code %errorlevel%
    pause
)

@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
title Monitor de Internet - Encerrar

if not exist ".venv\Scripts\python.exe" (
    echo  O Monitor de Internet não está instalado nesta pasta.
    pause
    exit /b 1
)

echo.
".venv\Scripts\python.exe" -m app.launcher parar
echo.
timeout /t 4 >nul
exit /b 0

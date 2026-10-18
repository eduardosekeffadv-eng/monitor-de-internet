@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
title Monitor de Internet

if not exist ".venv\Scripts\python.exe" goto :nao_instalado

echo.
echo  MONITOR DE INTERNET
echo  -------------------
".venv\Scripts\python.exe" -m app.launcher iniciar
if errorlevel 1 goto :falhou

echo.
echo  Esta janela fecha sozinha em alguns segundos.
timeout /t 6 >nul
exit /b 0

:nao_instalado
echo.
echo  [ERRO] O Monitor de Internet ainda não foi instalado.
echo  Execute primeiro o arquivo instalar.bat.
echo.
pause
exit /b 1

:falhou
echo.
pause
exit /b 1

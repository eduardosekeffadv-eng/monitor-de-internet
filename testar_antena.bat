@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
title Teste da antena Starlink

if not exist ".venv\Scripts\python.exe" (
    echo.
    echo  [ERRO] Coloque este arquivo e o teste_antena.py dentro da pasta
    echo  do Monitor de Internet, onde fica o instalar.bat, e rode de novo.
    echo.
    pause
    exit /b 1
)

echo.
echo  TESTE DE LEITURA DA ANTENA STARLINK
echo  -----------------------------------
echo  Somente leitura: nada é alterado na antena.
echo.
echo  [1/2] Instalando a biblioteca de comunicação com a antena...
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q starlink-grpc-core
if errorlevel 1 (
    echo  [ERRO] Não foi possível instalar a biblioteca. Verifique a internet.
    pause
    exit /b 1
)
echo  [2/2] Consultando a antena...
echo.
".venv\Scripts\python.exe" teste_antena.py
echo.
if exist "teste_antena_resultado.txt" start "" notepad "teste_antena_resultado.txt"
pause

@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
title Monitor de Internet - Instalação

echo.
echo  ==============================================
echo    MONITOR DE INTERNET - Instalação
echo  ==============================================
echo.

rem ---- 1. Localiza o Python (lançador "py" ou "python" do PATH) ----
set "PY="
py -3 --version >nul 2>&1 && set "PY=py -3"
if not defined PY (
    python --version >nul 2>&1 && set "PY=python"
)
if not defined PY goto :sem_python

%PY% -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
if errorlevel 1 goto :python_antigo

for /f "delims=" %%v in ('%PY% -c "import sys; print(sys.version.split()[0])"') do set "PYVER=%%v"
echo  [1/4] Python %PYVER% encontrado.

rem ---- 2. Cria o ambiente virtual ----
if exist ".venv\Scripts\python.exe" (
    echo  [2/4] Ambiente virtual já existe; será reaproveitado.
) else (
    echo  [2/4] Criando o ambiente virtual em .venv ...
    %PY% -m venv .venv
    if errorlevel 1 goto :erro_venv
)

rem ---- 3. Instala as dependências ----
echo  [3/4] Instalando dependências - pode levar um minuto ...
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check --upgrade pip >nul 2>&1
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r requirements.txt
if errorlevel 1 goto :erro_pip

rem ---- 3b. Componente opcional: dados da antena Starlink ----
echo  [3/4] Instalando o componente de leitura da antena Starlink ...
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q -r requirements-starlink.txt
if errorlevel 1 (
    echo  Aviso: não foi possível instalar o componente da antena Starlink.
    echo  O monitor funciona normalmente, apenas sem os dados da antena.
)

rem ---- 4. Verifica o ICMP nativo do Windows ----
echo  [4/4] Verificando a medição nativa do Windows ...
".venv\Scripts\python.exe" -m app.launcher verificar
if errorlevel 1 (
    echo.
    echo  Aviso: o teste de ICMP local falhou. O monitor ainda pode funcionar
    echo  usando o teste TCP alternativo. Veja o LEIAME.md, seção Problemas comuns.
)

if not exist "data" mkdir "data"

echo.
echo  ==============================================
echo    Instalação concluída!
echo    Para começar, execute: iniciar_monitoramento.bat
echo  ==============================================
echo.
pause
exit /b 0

:sem_python
echo  [ERRO] Python não encontrado.
echo.
echo  Instale o Python 3.10 ou mais recente em https://www.python.org/downloads/
echo  e marque a opção "Add python.exe to PATH" durante a instalação.
echo  Depois, execute este instalar.bat novamente.
echo.
pause
exit /b 1

:python_antigo
echo  [ERRO] É necessário o Python 3.10 ou mais recente.
echo  Instale uma versão atual em https://www.python.org/downloads/
echo.
pause
exit /b 1

:erro_venv
echo.
echo  [ERRO] Não foi possível criar o ambiente virtual.
echo  Verifique se a pasta não está protegida contra gravação
echo  e se o Python foi instalado com o módulo "venv".
echo.
pause
exit /b 1

:erro_pip
echo.
echo  [ERRO] Falha ao instalar as dependências.
echo  A instalação precisa de internet apenas nesta etapa.
echo  Verifique a conexão e execute o instalar.bat novamente.
echo.
pause
exit /b 1

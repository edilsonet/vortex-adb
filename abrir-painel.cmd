@echo off
REM ===================================================================
REM  ABRE O PAINEL "Registro ANAC"
REM
REM  Um duplo clique neste arquivo:
REM    1. se o servidor ja estiver no ar, so abre o navegador;
REM    2. se nao estiver, liga o servidor, espera ele responder
REM       e so entao abre o navegador.
REM
REM  Para DESLIGAR o servidor depois, feche a janela preta.
REM ===================================================================
setlocal

set "RAIZ=%~dp0"
set "URL=http://127.0.0.1:8730/"
set "SITUACAO=%URL%api/sessao"
set "PY=%RAIZ%.venv\Scripts\python.exe"
set "LOG=%RAIZ%build\_painel.log"

if not exist "%PY%" (
  echo [ERRO] Python do projeto nao encontrado:
  echo        %PY%
  echo.
  pause
  exit /b 1
)

REM --- O servidor ja esta no ar? ------------------------------------
REM /api/sessao e publica e devolve 200 mesmo sem cookie -- por isso
REM serve de sonda. (/api/estado exige login e devolveria 401, o que
REM o PowerShell trataria como falha.)
call :vivo
if not errorlevel 1 goto abre

echo Servidor desligado. Ligando agora...
if not exist "%RAIZ%build" mkdir "%RAIZ%build"

REM As variaveis sao herdadas pelo processo filho; `start` aponta
REM direto para o python -- sem `cmd /c` no meio, que com aspas
REM aninhadas quebrava o arranque.
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"
start "Registro ANAC - servidor (feche esta janela para parar)" /D "%RAIZ%" "%PY%" etl\app_server.py --sem-backfill > "%LOG%" 2>&1

echo Aguando o servidor responder...

REM --- Espera ate ~90 segundos --------------------------------------
set /a TENTATIVA=0
:aguarda
set /a TENTATIVA+=1
if %TENTATIVA% GTR 45 goto demorou

call :vivo
if not errorlevel 1 goto abre

ping -n 2 127.0.0.1 >nul
goto aguarda

:abre
echo.
echo   Painel: %URL%
echo   Login:  edilsonet@gmail.com
echo.
echo Para parar o servidor, feche a janela preta.
start "" "%URL%"
endlocal
exit /b 0

:demorou
echo.
echo [ERRO] O servidor nao respondeu em 90 segundos.
echo        log do arranque: %LOG%
echo.
pause
endlocal
exit /b 1

REM --- sonda: retorna errolevel 0 se o servidor responder -----------
:vivo
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "try { $null = Invoke-WebRequest -Uri 'http://127.0.0.1:8730/api/sessao' -UseBasicParsing -TimeoutSec 3; exit 0 } catch { if ($_.Exception.Response) { exit 0 } else { exit 1 } }" >nul 2>&1
exit /b %ERRORLEVEL%

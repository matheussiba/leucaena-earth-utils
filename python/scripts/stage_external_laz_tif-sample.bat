@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Stage LAZ+TIF  external HD  -^>  local OUTDIR
chcp 65001 >nul

REM =====================================================================
REM  Copie para: stage_external_laz_tif.bat  (local, fora do git)
REM  Edite a copia. O .bat real esta no .gitignore; este sample fica no repo.
REM
REM  Token: cole ABAIXO no set "ALS_MACHINE_TOKEN=...".
REM  Nao precisa de variavel de ambiente do Windows.
REM  O token NAO vai na linha do Python (o processo herda o ambiente do .bat).
REM
REM  Cota: preencha MAX_FILES ou MAX_GB.
REM  Se um ficar vazio, vale o outro.
REM  Se os dois ficarem vazios, copia tudo o que ainda nao esta no destino
REM  e que o leucaena.earth nao marcou como lidar_proc=1.
REM  Arquivos que ja estao em DEST nao entram na conta: 100 pedidos com
REM  50 ja copiados geram mais 100 arquivos novos.
REM =====================================================================

set "PYTHONUNBUFFERED=1"
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"
reg add "HKCU\Console" /v QuickEdit /t REG_DWORD /d 0 /f >nul 2>&1

REM >>> Pastas. SRC = HD externo. DEST = pasta local de processamento.
set "SRC=E:\tiles"
set "DEST=C:\OUTDIR"

REM >>> Cota. Deixe vazio com: set "MAX_FILES="
set "MAX_FILES=100"
set "MAX_GB="

set "ALS_API_URL=https://leucaena.earth"
REM >>> COLE O TOKEN DO RENDER AQUI (entre as aspas). Vazio = o script para.
set "ALS_MACHINE_TOKEN="

echo ============================================================
echo  Stage LAZ + optical (.tif) do HD externo
echo  Origem : %SRC%
echo  Destino: %DEST%
if "%MAX_FILES%"=="" (
  echo  Arquivos: sem limite
) else (
  echo  Arquivos: %MAX_FILES% novos ^(alem dos que ja estao no destino^)
)
if "%MAX_GB%"=="" (
  echo  GB     : sem limite
) else (
  echo  GB     : %MAX_GB%
)
if "%MAX_FILES%"=="" if "%MAX_GB%"=="" echo  Cota   : nenhuma ^> copia o que ainda falta
echo  API    : %ALS_API_URL%
if "%ALS_MACHINE_TOKEN%"=="" (
  echo  Token  : vazio = o Python recusa ^(nao copia as cegas^)
) else (
  echo  Token  : definido no .bat ^(nao exibido^)
)
echo  Pula   : nome ja presente em DEST, e tile com lidar_proc=1
echo ============================================================
echo.

if not exist "%~dp0stage_external_laz_tif.py" (
  echo [ERRO] Nao achei stage_external_laz_tif.py nesta pasta:
  echo   %~dp0
  echo.
  pause
  exit /b 2
)

if not exist "%SRC%\" (
  echo [ERRO] Pasta de origem nao existe: %SRC%
  echo Edite SRC neste .bat. Aponte para a pasta do HD externo.
  echo.
  pause
  exit /b 2
)

set "PY="
where python >nul 2>&1 && set "PY=python"
if not defined PY (
  where py >nul 2>&1 && set "PY=py -3"
)
if not defined PY (
  echo [ERRO] Python nao esta no PATH.
  echo.
  pause
  exit /b 2
)

set "FILES_ARG="
set "GB_ARG="
if not "%MAX_FILES%"=="" set "FILES_ARG=--max-files %MAX_FILES%"
if not "%MAX_GB%"=="" set "GB_ARG=--max-gb %MAX_GB%"

echo Usando: %PY%
echo Argumentos extras: %*
echo.

%PY% -u "%~dp0stage_external_laz_tif.py" --source "%SRC%" --dest "%DEST%" --als-api-url "%ALS_API_URL%" %FILES_ARG% %GB_ARG% %*
set "RC=%ERRORLEVEL%"

echo.
if "%RC%"=="3" (
  echo [AVISO] Outra copia deste script JA ESTA RODANDO ^(trava no destino^).
  echo         Nao abra duas janelas. Deixe a outra continuar; esta so avisa e sai.
  echo Codigo: 3
  echo.
  echo Esta janela fecha sozinha em 15 segundos...
  timeout /t 15 /nobreak >nul
  exit /b 3
)

echo Terminou com codigo %RC%.
echo Manifesto: %DEST%\stage_external_laz_tif_manifest.csv
echo.
if not "%RC%"=="0" (
  echo Se o codigo nao for 0, rode este .bat de novo.
  echo O que ja foi copiado nao e repetido, e a cota conta so arquivos novos.
)

REM Duplo clique: pausa para dar para ler. Ja dentro de um CMD aberto: nao pausa.
echo %cmdcmdline% | findstr /i /c:"%~f0" >nul
if %errorlevel%==0 pause
exit /b %RC%

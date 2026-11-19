@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"
title GeoTrees ALS
chcp 65001 >nul

REM =====================================================================
REM  Copie para: batch_geotrees_als.bat  (local, fora do git)
REM  Edite a copia. A janela SEMPRE fica aberta no fim (pause).
REM
REM  Token: cole ABAIXO no set "ALS_MACHINE_TOKEN=...".
REM  Nao precisa de variavel de ambiente do Windows.
REM  O token NAO vai na linha do Rscript (isso quebrava o .bat).
REM =====================================================================

call :main %*
set "RC=!ERRORLEVEL!"
echo.
echo Codigo de saida: !RC!
if "!RC!"=="3" echo Outra copia ainda segura a trava (.geotrees_als.lock).
echo.
echo Esta janela fica aberta para voce ler a mensagem.
pause
exit /b !RC!

:main
set "LAZ_DIR=D:\laz"
set "BLOCO="
set "OUT_DIR=G:\geotrees_als"
set "ARTICULACAO="
set "N_CORES=20"
set "LIMIT="
set "ALS_API_URL=https://leucaena.earth"
set "ALS_REPORT_EVERY=5"

REM true = verbose do pipeline; false = andamento + barra (recomendado)
set "DEBUG=false"

REM >>> COLE O TOKEN DO RENDER AQUI (entre as aspas). Vazio = API desligada.
set "ALS_MACHINE_TOKEN="

echo ============================================================
echo  GeoTrees ALS por tile
echo  Pasta bat: %~dp0
echo  LAZ     : %LAZ_DIR%
echo  BLOCO   : %BLOCO%  (vazio = pasta inteira)
echo  Saida   : %OUT_DIR%
echo  Nucleos : %N_CORES%
echo  API     : %ALS_API_URL%
if "!ALS_MACHINE_TOKEN!"=="" (
  echo  Token   : vazio = API desligada
) else (
  echo  Token   : definido no .bat ^(nao exibido^)
)
echo  Report  : a cada %ALS_REPORT_EVERY% tile(s)
echo  Debug   : %DEBUG%
echo  Produtos: %OUT_DIR%\processed\22^|23\^<tile^>
echo ============================================================
echo.

if not exist "%~dp0batch_geotrees_als.R" (
  echo [ERRO] Nao achei batch_geotrees_als.R nesta pasta:
  echo   %~dp0
  exit /b 2
)

set "RS="
where Rscript >nul 2>&1 && set "RS=Rscript"
if not defined RS (
  for /f "delims=" %%D in ('dir /b /ad /o-n "C:\Program Files\R" 2^>nul') do (
    if not defined RS if exist "C:\Program Files\R\%%D\bin\Rscript.exe" set "RS=C:\Program Files\R\%%D\bin\Rscript.exe"
  )
)
if not defined RS (
  for /f "delims=" %%D in ('dir /b /ad /o-n "C:\Program Files\R" 2^>nul') do (
    if not defined RS if exist "C:\Program Files\R\%%D\bin\x64\Rscript.exe" set "RS=C:\Program Files\R\%%D\bin\x64\Rscript.exe"
  )
)
if not defined RS (
  echo [ERRO] Rscript nao esta no PATH e nao achei em "C:\Program Files\R".
  exit /b 2
)

echo Usando: %RS%
echo Argumentos extras: %*
echo.

if not exist "%LAZ_DIR%\" (
  echo [ERRO] Pasta de LAZ nao existe: %LAZ_DIR%
  exit /b 2
)
if not exist "%OUT_DIR:~0,2%\" (
  echo [ERRO] O disco da saida nao esta acessivel: %OUT_DIR%
  exit /b 2
)

set "BLOCO_ARG="
set "CORES_ARG="
set "LIMIT_ARG="
set "ARTIC_ARG="
set "ALS_URL_ARG=--als-api-url "%ALS_API_URL%""
set "ALS_REP_ARG=--als-report-every %ALS_REPORT_EVERY%"
set "DEBUG_ARG=--no-debug"
if /I "%DEBUG%"=="true" set "DEBUG_ARG=--debug"
if /I "%DEBUG%"=="1" set "DEBUG_ARG=--debug"
if /I "%DEBUG%"=="yes" set "DEBUG_ARG=--debug"
if defined BLOCO set "BLOCO_ARG=--bloco %BLOCO%"
if defined N_CORES set "CORES_ARG=--n-cores %N_CORES%"
if defined LIMIT set "LIMIT_ARG=--limit %LIMIT%"
if defined ARTICULACAO set "ARTIC_ARG=--articulacao "%ARTICULACAO%""

REM Token so no set acima. Nao passa --als-api-token na linha do R.
echo Comando:
echo "%RS%" "%~dp0batch_geotrees_als.R" --laz-dir "%LAZ_DIR%" --out-dir "%OUT_DIR%" %BLOCO_ARG% %ARTIC_ARG% %CORES_ARG% %LIMIT_ARG% %ALS_URL_ARG% %ALS_REP_ARG% %DEBUG_ARG% %*
echo.

"%RS%" "%~dp0batch_geotrees_als.R" --laz-dir "%LAZ_DIR%" --out-dir "%OUT_DIR%" %BLOCO_ARG% %ARTIC_ARG% %CORES_ARG% %LIMIT_ARG% %ALS_URL_ARG% %ALS_REP_ARG% %DEBUG_ARG% %*
set "RC=%ERRORLEVEL%"

echo.
if not "%RC%"=="0" (
  echo [ERRO] O script terminou com codigo %RC%.
) else (
  echo Terminou com codigo 0.
)
echo Relatorio: %OUT_DIR%\geotrees_als_relatorio.md
echo Registro:  %OUT_DIR%\geotrees_als_registry.csv
echo GPKG:      %OUT_DIR%\geotrees_als_registro.gpkg
exit /b %RC%

@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"
title GeoTrees ALS LAStools
chcp 65001 >nul

REM =====================================================================
REM  Copie para: batch_geotrees_als_lastools.bat  (local, fora do git)
REM  Edite a copia.
REM  Codigo 3 (outra copia na trava): espera 15s e fecha (bom pro Agendador).
REM  Outros finais: pause para ler a mensagem.
REM
REM  Token: cole ABAIXO no set "ALS_MACHINE_TOKEN=...".
REM  Nao precisa de variavel de ambiente do Windows.
REM  O token NAO vai na linha do Rscript (isso quebrava o .bat).
REM  O R herda ALS_MACHINE_TOKEN so deste processo do .bat.
REM =====================================================================

call :main %*
set "RC=!ERRORLEVEL!"
echo.
echo Codigo de saida: !RC!
if "!RC!"=="3" (
  echo Outra copia ainda segura a trava ^(.geotrees_als_lastools.lock^).
  echo Esta instancia nao vai processar. Fechando em 15 segundos...
  timeout /t 15 /nobreak >nul
  exit /b !RC!
)
echo.
echo Esta janela fica aberta para voce ler a mensagem.
pause
exit /b !RC!

:main
REM =====================================================================
REM  VARIAVEIS PARA EDITAR (um unico bloco)
REM =====================================================================
set "LAZ_DIR=D:\laz"
set "BLOCO="
set "OUT_DIR=G:\geotrees_als_lastools"
set "ARTICULACAO="
set "PATH_LASTOOLS=C:\LAStools\bin"
REM vazio = sem limite de horas; ex. 12 = para entre tiles apos 12h
set "MAX_HOURS="
set "N_CORES=20"
set "LIMIT="
set "ALS_API_URL=https://leucaena.earth"
set "ALS_REPORT_EVERY=5"

REM true = verbose do pipeline (retilagem tile a tile etc.)
REM false = so andamento principal + barra de progresso (recomendado)
set "DEBUG=false"

REM >>> COLE O TOKEN DO RENDER AQUI (entre as aspas). Vazio = API desligada.
REM Uma unica linha. Nao deixe outro set ALS_MACHINE_TOKEN= vazio abaixo.
set "ALS_MACHINE_TOKEN="

REM NAS: copia async OUT_DIR\processed\<utm>\<tile>
REM   -> NAS_DIR\<Fehidro|Lote04|Voo22>\<BLOCO>\<tile>
REM Layer da articulacao: Articulacao_Laser_Fehidro / Lote4 / Voo22
REM Vazio = desliga copia NAS
set "NAS_DIR=O:\ALS_IGC"
REM true = apos copia OK no NAS, apaga a pasta local em OUT_DIR\processed
REM false = mantem local (padrao)
set "NAS_DELETE_LOCAL=false"
REM true = quando tile fecha com status 1, apaga o .laz em LAZ_DIR (padrao)
REM false = mantem o .laz
set "DELETE_LAZ_ON_OK=true"
REM true  = SP + fora; processa sp_inside=TRUE primeiro, depois FALSE
REM false = so tiles com sp_inside=TRUE (dentro de SP)
set "OUT_SP=true"
REM SKIP_HOLES: pula tile se buraco interior (cobertura) >= N hectares.
REM   20 = padrao (pula buracos >= 20 ha). 6 = >= 6 ha. 15 = >= 15 ha.
REM   vazio = nao checa / nao pula buracos (--no-skip-holes).
REM Checagem superfast (grade 250 m), como batch_check_laz_holes.py --superfast.
set "SKIP_HOLES=20"
REM =====================================================================

echo ============================================================
echo  GeoTrees ALS por tile (LAStools)
echo  Pasta bat: %~dp0
echo  LAZ     : %LAZ_DIR%
echo  BLOCO   : %BLOCO%  (vazio = pasta inteira)
echo  Saida   : %OUT_DIR%
echo  LAStools: %PATH_LASTOOLS%
if "!MAX_HOURS!"=="" (
  echo  Horas   : indefinido ^(sem limite^)
) else (
  echo  Horas   : !MAX_HOURS!
)
echo  Nucleos : %N_CORES%
echo  API     : %ALS_API_URL%
if "!ALS_MACHINE_TOKEN!"=="" (
  echo  Token   : vazio = API desligada
) else (
  echo  Token   : definido no .bat ^(nao exibido^)
)
echo  Report  : a cada %ALS_REPORT_EVERY% tile(s)
echo  Debug   : %DEBUG%
echo  NAS     : %NAS_DIR%
echo  NAS del : %NAS_DELETE_LOCAL%  ^(apagar OUTDIR apos copia^)
echo  Del LAZ : %DELETE_LAZ_ON_OK%  ^(apagar .laz quando status 1^)
echo  OUT_SP  : %OUT_SP%  ^(true=SP primeiro depois fora; false=so SP^)
if "!SKIP_HOLES!"=="" (
  echo  Holes   : desligado ^(SKIP_HOLES vazio^)
) else (
  echo  Holes   : pula se buraco ^>= !SKIP_HOLES! ha ^(superfast^)
)
echo  Produtos: %OUT_DIR%\processed\22^|23\^<tile^>
echo ============================================================
echo.

if not exist "%~dp0batch_geotrees_als_lastools.R" (
  echo [ERRO] Nao achei batch_geotrees_als_lastools.R nesta pasta:
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
if not exist "%PATH_LASTOOLS%\" (
  echo [ERRO] Pasta do LAStools nao existe: %PATH_LASTOOLS%
  exit /b 2
)

set "BLOCO_ARG="
set "CORES_ARG="
set "LIMIT_ARG="
set "ARTIC_ARG="
set "LASTOOLS_ARG=--path-lastools "%PATH_LASTOOLS%""
set "HOURS_ARG="
if defined MAX_HOURS if not "%MAX_HOURS%"=="" set "HOURS_ARG=--max-hours %MAX_HOURS%"
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

set "NAS_ARG="
if defined NAS_DIR if not "%NAS_DIR%"=="" set "NAS_ARG=--nas-dir "%NAS_DIR%""
set "NAS_DEL_ARG=--no-nas-delete-local"
if /I "%NAS_DELETE_LOCAL%"=="true" set "NAS_DEL_ARG=--nas-delete-local"
if /I "%NAS_DELETE_LOCAL%"=="1" set "NAS_DEL_ARG=--nas-delete-local"
if /I "%NAS_DELETE_LOCAL%"=="yes" set "NAS_DEL_ARG=--nas-delete-local"
set "DEL_LAZ_ARG=--delete-laz-on-ok"
if /I "%DELETE_LAZ_ON_OK%"=="false" set "DEL_LAZ_ARG=--no-delete-laz-on-ok"
if /I "%DELETE_LAZ_ON_OK%"=="0" set "DEL_LAZ_ARG=--no-delete-laz-on-ok"
if /I "%DELETE_LAZ_ON_OK%"=="no" set "DEL_LAZ_ARG=--no-delete-laz-on-ok"
if /I "%DELETE_LAZ_ON_OK%"=="nao" set "DEL_LAZ_ARG=--no-delete-laz-on-ok"
set "OUT_SP_ARG=--out-sp"
if /I "%OUT_SP%"=="false" set "OUT_SP_ARG=--no-out-sp"
if /I "%OUT_SP%"=="0" set "OUT_SP_ARG=--no-out-sp"
if /I "%OUT_SP%"=="no" set "OUT_SP_ARG=--no-out-sp"
if /I "%OUT_SP%"=="nao" set "OUT_SP_ARG=--no-out-sp"
set "SKIP_HOLES_ARG=--no-skip-holes"
if defined SKIP_HOLES if not "%SKIP_HOLES%"=="" set "SKIP_HOLES_ARG=--skip-holes %SKIP_HOLES%"

REM Token so no ambiente deste .bat (set acima). Nao passa --als-api-token.
echo Comando:
echo "%RS%" "%~dp0batch_geotrees_als_lastools.R" --laz-dir "%LAZ_DIR%" --out-dir "%OUT_DIR%" %BLOCO_ARG% %ARTIC_ARG% %CORES_ARG% %LIMIT_ARG% %LASTOOLS_ARG% %HOURS_ARG% %ALS_URL_ARG% %ALS_REP_ARG% %DEBUG_ARG% %NAS_ARG% %NAS_DEL_ARG% %DEL_LAZ_ARG% %OUT_SP_ARG% %SKIP_HOLES_ARG% %*
echo.

"%RS%" "%~dp0batch_geotrees_als_lastools.R" --laz-dir "%LAZ_DIR%" --out-dir "%OUT_DIR%" %BLOCO_ARG% %ARTIC_ARG% %CORES_ARG% %LIMIT_ARG% %LASTOOLS_ARG% %HOURS_ARG% %ALS_URL_ARG% %ALS_REP_ARG% %DEBUG_ARG% %NAS_ARG% %NAS_DEL_ARG% %DEL_LAZ_ARG% %OUT_SP_ARG% %SKIP_HOLES_ARG% %*
set "RC=%ERRORLEVEL%"

echo.
if not "%RC%"=="0" (
  echo [ERRO] O script terminou com codigo %RC%.
) else (
  echo Terminou com codigo 0.
)
echo Report:    %OUT_DIR%\geotrees_als_report.md
echo Registry:  %OUT_DIR%\geotrees_als_registry.gpkg
echo NAS log:   %OUT_DIR%\geotrees_als_nas_log.csv
echo Stop file: %OUT_DIR%\STOP.txt
exit /b %RC%

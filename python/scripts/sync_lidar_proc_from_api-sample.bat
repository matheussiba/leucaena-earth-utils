@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Sync lidar_proc from leucaena.earth -^> articulacao GPKG
chcp 65001 >nul

REM =====================================================================
REM  Copie para: sync_lidar_proc_from_api.bat  (local, fora do git)
REM  Edite a copia. O .bat real pode ficar no .gitignore; este sample fica no repo.
REM
REM  Espelha status_code=1 da API no campo lidar_proc do GPKG de articulacao.
REM =====================================================================

set "PYTHONUNBUFFERED=1"
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"

set "ALS_API_URL=https://leucaena.earth"
REM >>> COLE O TOKEN DO RENDER AQUI (entre as aspas).
set "ALS_MACHINE_TOKEN="

REM false = so atualiza tiles com sp_inside=1 (dentro de SP)
REM true  = atualiza SP + fora de SP (sp_inside=0 tambem)
set "OUT_SP=false"

REM Vazio = usa o GPKG padrao do clone (data/articulacao_igc_sp/...)
set "GPKG="

echo ============================================================
echo  Sync lidar_proc API -^> articulacao GPKG
echo  API    : %ALS_API_URL%
echo  OUT_SP : %OUT_SP%  ^(false=so SP / true=SP+fora^)
if "%ALS_MACHINE_TOKEN%"=="" (
  echo  Token  : vazio = o Python recusa
) else (
  echo  Token  : definido no .bat ^(nao exibido^)
)
echo ============================================================
echo.

if not exist "%~dp0sync_lidar_proc_from_api.py" (
  echo [ERRO] Nao achei sync_lidar_proc_from_api.py nesta pasta.
  pause
  exit /b 2
)

set "PY="
where python >nul 2>&1 && set "PY=python"
if not defined PY where py >nul 2>&1 && set "PY=py -3"
if not defined PY (
  echo [ERRO] Python nao esta no PATH.
  pause
  exit /b 2
)

set "GPKG_ARG="
if not "%GPKG%"=="" set "GPKG_ARG=--gpkg "%GPKG%""

%PY% -u "%~dp0sync_lidar_proc_from_api.py" --api-url "%ALS_API_URL%" --out-sp %OUT_SP% %GPKG_ARG% %*
set "RC=%ERRORLEVEL%"
echo.
echo Terminou com codigo %RC%.
echo %cmdcmdline% | findstr /i /c:"%~f0" >nul
if %errorlevel%==0 pause
exit /b %RC%

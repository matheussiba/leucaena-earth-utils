@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title LAZ holes  D:\laz  -^>  D:\laz_holes_igc.gpkg
chcp 65001 >nul
set "PYTHONUNBUFFERED=1"
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"

echo ============================================================
echo  Batch buracos de cobertura nos LAZ IGC
echo  Entrada : D:\laz
echo  GPKG    : D:\laz_holes_igc.gpkg
echo  CSV     : D:\laz_holes_summary.csv / D:\laz_holes_registry.csv
echo  MD      : D:\laz_holes_relatorio.md
echo  Retoma sozinho; use --deadline-min 25 se tiver reuniao.
echo  So regenerar o .md:  --report-only
echo ============================================================
echo.

set "PY="
where python >nul 2>&1 && set "PY=python"
if not defined PY (
    where py >nul 2>&1 && set "PY=py -3"
)
if not defined PY (
    echo [ERRO] Python nao esta no PATH.
    echo Instale Python ou abra o OSGeo4W / prompt do QGIS e rode este .bat de la.
    echo.
    pause
    exit /b 2
)

echo Usando: %PY%
echo Argumentos extras: %*
echo.
echo Sugestao reuniao 30 min:
echo   %PY% -u "%~dp0batch_check_laz_holes.py" --fast --deadline-min 25
echo So atualizar o .md com o que ja rodou:
echo   %PY% -u "%~dp0batch_check_laz_holes.py" --report-only
echo.

%PY% -u "%~dp0batch_check_laz_holes.py" %*
set "RC=%ERRORLEVEL%"

echo.
echo Terminou com codigo %RC%.
echo GPKG:    D:\laz_holes_igc.gpkg
echo Summary: D:\laz_holes_summary.csv
echo Registry:D:\laz_holes_registry.csv
echo MD:      D:\laz_holes_relatorio.md
echo.
echo Se parou no deadline ou Ctrl+C, rode de novo para continuar.
echo Se so quiser o .md agora: rode com --report-only.

echo %cmdcmdline% | findstr /i /c:"%~f0" >nul
if %errorlevel%==0 pause
exit /b %RC%

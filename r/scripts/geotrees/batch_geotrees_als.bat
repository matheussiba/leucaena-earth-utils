@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title GeoTrees ALS
chcp 65001 >nul

REM =====================================================================
REM  Edite aqui. O resto pode ir na linha de comando.
REM  BLOCO vazio = todos os .laz/.las da pasta.
REM  Um bloco:    set "BLOCO=EF"
REM  Dois blocos: set "BLOCO=EF,2024"
REM  ARTICULACAO vazio = articulacao-igc-merged-spatialindex-sirgas2000.gpkg
REM  na mesma pasta deste .bat, sem subpasta.
REM =====================================================================
set "LAZ_DIR=D:\laz"
set "BLOCO="
set "OUT_DIR=G:\geotrees_als"
set "ARTICULACAO="

REM Opcional. Vazio = nucleos fisicos - 2.
set "N_CORES="
REM Opcional. Vazio = sem limite. 1 = so o primeiro tile novo (teste).
set "LIMIT="

echo ============================================================
echo  GeoTrees ALS por tile
echo  LAZ     : %LAZ_DIR%
echo  BLOCO   : %BLOCO%  (vazio = pasta inteira)
echo  Saida   : %OUT_DIR%
echo  Produtos: %OUT_DIR%\processed\^<tile^>
echo  GPKG    : %OUT_DIR%\geotrees_als_registro.gpkg
echo  CSV     : %OUT_DIR%\geotrees_als_registry.csv
echo  MD      : %OUT_DIR%\geotrees_als_relatorio.md
echo.
echo  Pula tile que ja tem _INFO_summary_processing.csv nesta saida.
echo  Se parar no meio, rode de novo: continua do que falta.
echo  O .laz interrompido comeca de novo; os que ja terminaram nao.
echo  A cada arquivo a previsao do que falta e recalculada (dias, horas, minutos).
echo  Erro entra de novo na proxima rodada. --force refaz tudo.
echo  Teste sem processar:  --dry-run     ou  --list-blocos
echo  So o relatorio:       --report-only
echo  Parar entre tiles: crie %OUT_DIR%\PARAR.txt
echo ============================================================
echo.

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
    echo Instale o R e rode este .bat de novo.
    echo.
    pause
    exit /b 2
)

echo Usando: %RS%
echo Argumentos extras: %*
echo.

if not exist "%LAZ_DIR%\" (
    echo [ERRO] Pasta de LAZ nao existe: %LAZ_DIR%
    echo.
    pause
    exit /b 2
)
if not exist "%OUT_DIR:~0,2%\" (
    echo [ERRO] O disco da saida nao esta acessivel: %OUT_DIR%
    echo Confira se o disco G: esta conectado.
    echo.
    pause
    exit /b 2
)

set "BLOCO_ARG="
set "CORES_ARG="
set "LIMIT_ARG="
set "ARTIC_ARG="
if defined BLOCO set "BLOCO_ARG=--bloco %BLOCO%"
if defined N_CORES set "CORES_ARG=--n-cores %N_CORES%"
if defined LIMIT set "LIMIT_ARG=--limit %LIMIT%"
if defined ARTICULACAO set "ARTIC_ARG=--articulacao "%ARTICULACAO%""

echo Comando:
echo "%RS%" "%~dp0batch_geotrees_als.R" --laz-dir "%LAZ_DIR%" --out-dir "%OUT_DIR%" %BLOCO_ARG% %ARTIC_ARG% %CORES_ARG% %LIMIT_ARG% %*
echo.

"%RS%" "%~dp0batch_geotrees_als.R" --laz-dir "%LAZ_DIR%" --out-dir "%OUT_DIR%" %BLOCO_ARG% %ARTIC_ARG% %CORES_ARG% %LIMIT_ARG% %*
set "RC=%ERRORLEVEL%"

echo.
if not "%RC%"=="0" (
    echo [ERRO] O script terminou com codigo %RC%.
    echo Leia as linhas acima. Esta janela fica aberta.
) else (
    echo Terminou com codigo 0.
)
echo Relatorio: %OUT_DIR%\geotrees_als_relatorio.md
echo Registro:  %OUT_DIR%\geotrees_als_registry.csv
echo GPKG:      %OUT_DIR%\geotrees_als_registro.gpkg
echo.
echo Se parou no meio ou deu erro em algum tile, rode este .bat de novo.
echo O que ja terminou nesta maquina nao sera refeito.
echo.
pause
exit /b %RC%
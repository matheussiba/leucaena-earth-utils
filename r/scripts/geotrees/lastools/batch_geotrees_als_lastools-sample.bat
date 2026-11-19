@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title GeoTrees ALS LAStools
chcp 65001 >nul

REM =====================================================================
REM  Edite aqui. O resto pode ir na linha de comando.
REM  BLOCO vazio = todos os .laz/.las da pasta.
REM  Um bloco:    set "BLOCO=EF"
REM  Dois blocos: set "BLOCO=EF,2024"
REM  ARTICULACAO vazio = data\articulacao_igc_sp\ no clone (obrigatoria).
REM  O campo epsg da articulacao define o CRS do tile:
REM    31983 -> EPSG:32723 | 31982 -> EPSG:32722
REM  Metadata GCA fica inline no .R (nao precisa de _GCA_metadata.csv).
REM  MAX_HOURS vazio = 24. Ex.: set "MAX_HOURS=8"
REM =====================================================================
set "LAZ_DIR=D:\laz"
set "BLOCO="
set "OUT_DIR=G:\geotrees_als_lastools"
set "ARTICULACAO="
set "PATH_LASTOOLS=C:\LAStools\bin"
set "MAX_HOURS="

REM Nucleos passados ao pipeline LAStools. 20 = padrao do laboratorio.
REM Vazio = nucleos fisicos - 2.
set "N_CORES=20"
REM Opcional. Vazio = sem limite de tiles. 1 = so o primeiro tile novo (teste).
set "LIMIT="

if not defined MAX_HOURS set "MAX_HOURS=24"

echo ============================================================
echo  GeoTrees ALS por tile (LAStools)
echo  LAZ     : %LAZ_DIR%
echo  BLOCO   : %BLOCO%  (vazio = pasta inteira)
echo  Saida   : %OUT_DIR%
echo  LAStools: %PATH_LASTOOLS%
echo  Horas   : %MAX_HOURS%  (para entre tiles; vazio no topo vira 24)
echo  Nucleos : %N_CORES%  (vazio = fisicos - 2)
echo  Produtos: %OUT_DIR%\processed\^<tile^>
echo  GPKG    : %OUT_DIR%\geotrees_als_registro.gpkg
echo  CSV     : %OUT_DIR%\geotrees_als_registry.csv
echo  MD      : %OUT_DIR%\geotrees_als_relatorio.md
echo.
echo  Metadata GCA: inline. CRS por tile via articulacao.epsg.
echo  31983-^>32723 | 31982-^>32722. Mistura: um fuso inteiro, depois o outro.
echo  Pula tile que ja tem _INFO_summary_processing.csv nesta saida.
echo  Se parar no meio, rode de novo: continua do que falta.
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
    echo Confira se o disco esta conectado.
    echo.
    pause
    exit /b 2
)
if not exist "%PATH_LASTOOLS%\" (
    echo [ERRO] Pasta do LAStools nao existe: %PATH_LASTOOLS%
    echo Ajuste PATH_LASTOOLS neste .bat.
    echo.
    pause
    exit /b 2
)

set "BLOCO_ARG="
set "CORES_ARG="
set "LIMIT_ARG="
set "ARTIC_ARG="
set "LASTOOLS_ARG=--path-lastools "%PATH_LASTOOLS%""
set "HOURS_ARG=--max-hours %MAX_HOURS%"
if defined BLOCO set "BLOCO_ARG=--bloco %BLOCO%"
if defined N_CORES set "CORES_ARG=--n-cores %N_CORES%"
if defined LIMIT set "LIMIT_ARG=--limit %LIMIT%"
if defined ARTICULACAO set "ARTIC_ARG=--articulacao "%ARTICULACAO%""

echo Comando:
echo "%RS%" "%~dp0batch_geotrees_als_lastools.R" --laz-dir "%LAZ_DIR%" --out-dir "%OUT_DIR%" %BLOCO_ARG% %ARTIC_ARG% %CORES_ARG% %LIMIT_ARG% %LASTOOLS_ARG% %HOURS_ARG% %*
echo.

"%RS%" "%~dp0batch_geotrees_als_lastools.R" --laz-dir "%LAZ_DIR%" --out-dir "%OUT_DIR%" %BLOCO_ARG% %ARTIC_ARG% %CORES_ARG% %LIMIT_ARG% %LASTOOLS_ARG% %HOURS_ARG% %*
set "RC=%ERRORLEVEL%"

REM 3 = outra copia ainda segura a trava. Fecha esta janela sem pause.
if "%RC%"=="3" exit /b 3

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
echo Se parou no meio, no limite de horas ou deu erro em algum tile, rode de novo.
echo O que ja terminou nesta saida nao sera refeito.
echo.
pause
exit /b %RC%

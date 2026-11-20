@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title RGBNIR  E:\RGB + F:\IR  -^>  G:\RGBNIR
chcp 65001 >nul

REM =====================================================================
REM  Copie para: build_rgbnir_from_rgb_ir.bat  (local, fora do git)
REM  Edite a copia (caminhos E:/F:/G: etc.).
REM =====================================================================

set "PYTHONUNBUFFERED=1"
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"
REM Evita o CMD "congelar" o print ate clicar/redimensionar (Quick Edit).
reg add "HKCU\Console" /v QuickEdit /t REG_DWORD /d 0 /f >nul 2>&1

echo ============================================================
echo  RGB + IR  -^>  RGBNIR de 4 bandas
echo  RGB    E:\RGB
echo  IR     F:\IR
echo  Saida  G:\RGBNIR
echo  Retoma sozinho: o que ja estiver em G:\RGBNIR e pulado.
echo  Planilhas: rgbnir_summary.csv e rgbnir_registry.csv
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

%PY% -u "%~dp0build_rgbnir_from_rgb_ir.py" %*
set "RC=%ERRORLEVEL%"

echo.
if "%RC%"=="3" (
    echo [AVISO] Outra copia deste script JA ESTA RODANDO ^(trava no disco de saida^).
    echo         Nao abra duas janelas. Deixe a outra continuar; esta so avisa e sai.
    echo Codigo: 3
) else (
    echo Terminou com codigo %RC%.
    echo Resumo:      G:\RGBNIR\rgbnir_summary.csv
    echo Registro:    G:\RGBNIR\rgbnir_registry.csv
    echo Dicionario:  G:\RGBNIR\rgbnir_registry_dicionario.csv
    echo.
    echo Se o codigo nao for 0, rode este .bat de novo para continuar do que falta.
)

REM Duplo clique: pausa para da para ler. Ja dentro de um CMD aberto: nao pausa.
echo %cmdcmdline% | findstr /i /c:"%~f0" >nul
if %errorlevel%==0 pause
exit /b %RC%

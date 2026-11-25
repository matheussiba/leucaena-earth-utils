@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Transferir LAZ RGB IR por AOI
chcp 65001 >nul

REM =====================================================================
REM  Copie para: transferir_laz_rgb_ir_v3_por_aoi.bat  (local, fora do git)
REM  Edite a copia. O .bat real esta no .gitignore; este sample fica no repo.
REM
REM  AOI vazio = sem recorte espacial.
REM  Os filtros SP_OUT e MAX_WATER_PERCENT continuam valendo.
REM  Para copiar a articulacao inteira, sem filtro de SP nem de agua:
REM    SP_OUT=true
REM    MAX_WATER_PERCENT=   (vazio)
REM
REM  DEST no CMD: nao use set "DEST=G:\" (a barra come a aspa).
REM  G:\. e a raiz do disco. O Python normaliza para G:\
REM
REM  Argumento extra vai para o Python. Exemplo so de vistoria, sem copiar
REM  e sem apagar:
REM    transferir_laz_rgb_ir_v3_por_aoi.bat --dry-run
REM =====================================================================

set "PYTHONUNBUFFERED=1"
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"
reg add "HKCU\Console" /v QuickEdit /t REG_DWORD /d 0 /f >nul 2>&1

REM >>> AOI. Vazio = todos os grids que passarem nos filtros abaixo.
set "AOI="

REM >>> Entrada. LIDAR le a pasta de .laz.
set "SOURCE_LAZ=D:\laz"
set "SOURCE_RGB=D:\rgb"
set "SOURCE_IR=D:\ir"
REM >>> Destino. O script cria laz\, rgb\ e ir\ aqui, uma para cada tipo true.
REM >>> G:\. e a raiz de G: (o Python normaliza para G:\).
set "DEST=G:\."

REM >>> true = copia essa pasta para o destino. false = pula.
REM >>> LIDAR grava em DEST\laz. RGB em DEST\rgb. IR em DEST\ir.
set "LIDAR=true"
set "RGB=true"
set "IR=true"

REM >>> Cobertura. Vazio = nao grava.
set "COBERTURA_OUT="

REM >>> false = so sp_inside=true (dentro de SP). true = tambem fora de SP.
set "SP_OUT=false"

REM >>> So grids com water_percent menor que este numero.
REM >>> Vazio (set "MAX_WATER_PERCENT=") = ignora a coluna e pega qualquer agua.
set "MAX_WATER_PERCENT=50"

REM >>> true = APAGA .laz ja em DEST\laz que ficaram fora de SP_OUT e de
REM >>> MAX_WATER_PERCENT neste run. Nao usa a AOI.
REM >>> Fora de SP (quando SP_OUT=false) e agua >= MAX_WATER_PERCENT saem.
REM >>> Nome sem grid, ou campo ausente, nao e apagado.
REM >>> false = nao apaga nada. Deixe false salvo no caso em que voce quer limpar.
set "DELETE_LAZ_IN_OUTPUTDIR_OUT_PARAMETERS=false"

echo ============================================================
echo  Transferir LAZ + RGB + IR
echo  AOI     : %AOI%
if "%AOI%"=="" echo            ^(vazio = sem recorte espacial^)
echo  LIDAR   : %LIDAR%    entrada %SOURCE_LAZ%    saida %DEST%\laz
echo  RGB     : %RGB%    entrada %SOURCE_RGB%    saida %DEST%\rgb
echo  IR      : %IR%    entrada %SOURCE_IR%    saida %DEST%\ir
echo  Destino : %DEST%   ^(cria laz\ rgb\ ir\ so para os tipos true^)
echo  Cobertura: %COBERTURA_OUT%
if "%COBERTURA_OUT%"=="" echo            ^(vazio = nao grava^)
echo  SP_OUT  : %SP_OUT%
if /I "%SP_OUT%"=="true" (
  echo            true = dentro e fora de SP
) else (
  echo            false = so sp_inside=true
)
if "%MAX_WATER_PERCENT%"=="" (
  echo  Agua    : sem limite
) else (
  echo  Agua    : water_percent ^< %MAX_WATER_PERCENT%
)
echo  Apagar LAZ fora dos filtros: %DELETE_LAZ_IN_OUTPUTDIR_OUT_PARAMETERS%
echo ============================================================
echo.

if not exist "%~dp0transferir_laz_rgb_ir_v3_por_aoi.py" (
  echo [ERRO] Nao achei transferir_laz_rgb_ir_v3_por_aoi.py nesta pasta:
  echo   %~dp0
  echo.
  pause
  exit /b 2
)

set "COPY_LIDAR=1"
set "COPY_RGB=1"
set "COPY_IR=1"
if /I "%LIDAR%"=="false" set "COPY_LIDAR=0"
if /I "%LIDAR%"=="0" set "COPY_LIDAR=0"
if /I "%LIDAR%"=="no" set "COPY_LIDAR=0"
if /I "%LIDAR%"=="nao" set "COPY_LIDAR=0"
if /I "%RGB%"=="false" set "COPY_RGB=0"
if /I "%RGB%"=="0" set "COPY_RGB=0"
if /I "%RGB%"=="no" set "COPY_RGB=0"
if /I "%RGB%"=="nao" set "COPY_RGB=0"
if /I "%IR%"=="false" set "COPY_IR=0"
if /I "%IR%"=="0" set "COPY_IR=0"
if /I "%IR%"=="no" set "COPY_IR=0"
if /I "%IR%"=="nao" set "COPY_IR=0"
if "%COPY_LIDAR%"=="0" if "%COPY_RGB%"=="0" if "%COPY_IR%"=="0" (
  echo [ERRO] LIDAR, RGB e IR estao desligados. Ligue pelo menos um.
  echo.
  pause
  exit /b 2
)
if "%COPY_LIDAR%"=="1" if not exist "%SOURCE_LAZ%\" (
  echo [ERRO] Pasta de LAZ nao existe: %SOURCE_LAZ%
  echo.
  pause
  exit /b 2
)
if "%COPY_RGB%"=="1" if not exist "%SOURCE_RGB%\" (
  echo [ERRO] Pasta de RGB nao existe: %SOURCE_RGB%
  echo.
  pause
  exit /b 2
)
if "%COPY_IR%"=="1" if not exist "%SOURCE_IR%\" (
  echo [ERRO] Pasta de IR nao existe: %SOURCE_IR%
  echo.
  pause
  exit /b 2
)
if not exist "%DEST%\" (
  echo [ERRO] Destino nao esta acessivel: %DEST%
  echo.
  pause
  exit /b 2
)
if not "%AOI%"=="" if not exist "%AOI%" (
  echo [ERRO] AOI nao encontrada: %AOI%
  echo Deixe AOI vazio para nao recortar.
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

set "AOI_ARG="
set "COBERTURA_ARG="
set "WATER_ARG="
set "DELETE_ARG="
set "LIDAR_ARG="
set "RGB_ARG="
set "IR_ARG="
set "SP_OUT_ARG=--no-sp-out"
if "%COPY_LIDAR%"=="0" set "LIDAR_ARG=--no-laz"
if "%COPY_RGB%"=="0" set "RGB_ARG=--no-rgb"
if "%COPY_IR%"=="0" set "IR_ARG=--no-ir"
if not "%AOI%"=="" set AOI_ARG=--aoi "%AOI%"
if not "%COBERTURA_OUT%"=="" set COBERTURA_ARG=--cobertura-out "%COBERTURA_OUT%"
if not "%MAX_WATER_PERCENT%"=="" set WATER_ARG=--max-water-percent %MAX_WATER_PERCENT%
if /I "%SP_OUT%"=="true" set "SP_OUT_ARG=--sp-out"
if /I "%SP_OUT%"=="1" set "SP_OUT_ARG=--sp-out"
if /I "%SP_OUT%"=="yes" set "SP_OUT_ARG=--sp-out"
if /I "%SP_OUT%"=="sim" set "SP_OUT_ARG=--sp-out"
if /I "%DELETE_LAZ_IN_OUTPUTDIR_OUT_PARAMETERS%"=="true" set "DELETE_ARG=--delete-laz-out-of-parameters"
if /I "%DELETE_LAZ_IN_OUTPUTDIR_OUT_PARAMETERS%"=="1" set "DELETE_ARG=--delete-laz-out-of-parameters"
if /I "%DELETE_LAZ_IN_OUTPUTDIR_OUT_PARAMETERS%"=="yes" set "DELETE_ARG=--delete-laz-out-of-parameters"
if /I "%DELETE_LAZ_IN_OUTPUTDIR_OUT_PARAMETERS%"=="sim" set "DELETE_ARG=--delete-laz-out-of-parameters"

if defined DELETE_ARG (
  echo ************************************************************
  echo  ATENCAO: vai APAGAR .laz em %DEST%\laz
  echo  que estiverem fora de SP_OUT e de MAX_WATER_PERCENT.
  echo  A AOI nao protege esses arquivos.
  echo ************************************************************
  echo.
)

echo Usando: %PY%
echo Argumentos extras: %*
echo.

%PY% -u "%~dp0transferir_laz_rgb_ir_v3_por_aoi.py" --source-laz "%SOURCE_LAZ%" --source-rgb "%SOURCE_RGB%" --source-ir "%SOURCE_IR%" --dest "%DEST%" %LIDAR_ARG% %RGB_ARG% %IR_ARG% %AOI_ARG% %COBERTURA_ARG% %SP_OUT_ARG% %WATER_ARG% %DELETE_ARG% %*
set "RC=%ERRORLEVEL%"

echo.
echo Terminou com codigo %RC%.
echo.
if not "%RC%"=="0" (
  echo Se o codigo nao for 0, nada alem do que o script ja imprimiu foi concluido.
)

REM Duplo clique: pausa para dar para ler. Ja dentro de um CMD aberto: nao pausa.
echo %cmdcmdline% | findstr /i /c:"%~f0" >nul
if %errorlevel%==0 pause
exit /b %RC%

@echo off
setlocal
cd /d "%~dp0"
title QQ Bot Project Manager
if not defined QINGLAN_CFG_WEB_PORT set "QINGLAN_CFG_WEB_PORT=8088"

echo ==============================================
echo QQ Bot Project Manager
echo Open http://127.0.0.1:%QINGLAN_CFG_WEB_PORT% after startup
echo ==============================================
echo.

set "PY_EXE=%~dp0.venv\Scripts\python.exe"
set "PY_ARGS="
if exist "%PY_EXE%" (
    "%PY_EXE%" -c "import flask" >nul 2>&1
    if not errorlevel 1 goto run
    "%PY_EXE%" -m pip install flask==3.0.0
    if not errorlevel 1 goto run
)

where py >nul 2>&1
if errorlevel 1 goto try_python
set "PY_EXE=py"
set "PY_ARGS=-3.12"
"%PY_EXE%" %PY_ARGS% -c "import flask" >nul 2>&1
if not errorlevel 1 goto run

set "PY_ARGS=-3"
"%PY_EXE%" %PY_ARGS% -c "import flask" >nul 2>&1
if not errorlevel 1 goto run
"%PY_EXE%" %PY_ARGS% -c "import sys" >nul 2>&1
if errorlevel 1 goto try_python
goto bootstrap

:try_python
where python >nul 2>&1
if errorlevel 1 goto missing
set "PY_EXE=python"
set "PY_ARGS="
"%PY_EXE%" -c "import flask" >nul 2>&1
if not errorlevel 1 goto run

:bootstrap
echo Installing the minimal WebUI dependency Flask...
"%PY_EXE%" %PY_ARGS% -m pip install flask==3.0.0
if errorlevel 1 goto missing

:run
start "QQ Bot Admin Server" /D "%~dp0" "%PY_EXE%" %PY_ARGS% -u "%~dp0config_web.py"
for /L %%I in (1,1,12) do (
    timeout /t 1 /nobreak >nul
    "%PY_EXE%" %PY_ARGS% -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:%QINGLAN_CFG_WEB_PORT%/api/health', timeout=1)" >nul 2>&1
    if not errorlevel 1 goto ready
)
echo ERROR: WebUI did not become ready at http://127.0.0.1:%QINGLAN_CFG_WEB_PORT%
echo Check the QQ Bot Admin Server window for startup errors.
pause
exit /b 1

:ready
start "" "http://127.0.0.1:%QINGLAN_CFG_WEB_PORT%"
echo.
echo Server launched. Close its window or press Ctrl+C there to stop it.
echo.
pause
exit /b 0

:missing
echo ERROR: Python with Flask was not found.
echo Install dependencies with a working Python interpreter.
pause
exit /b 1

@echo off
REM ============================================================
REM  Service Control (stop / status / doctor)
REM  ============================================================
REM  启服务请用: scripts\start_api.bat  /  scripts\start_workers.bat
REM
REM  用法:
REM    start_local.bat                  (默认: status)
REM    start_local.bat stop
REM    start_local.bat status
REM    start_local.bat doctor
REM    start_local.bat test
REM    start_local.bat restart
REM ============================================================
chcp 65001 >nul
setlocal

set SCRIPT_DIR=%~dp0
set ROOT_DIR=%SCRIPT_DIR%..

REM 优先用 uv run python (无需激活 venv，自动用 backend 项目依赖)
where uv >nul 2>&1
if %ERRORLEVEL% == 0 (
    set RUNNER=uv run python
) else (
    set PY=%ROOT_DIR%\backend\.venv\Scripts\python.exe
    if not exist "!PY!" (
        echo [ERR] uv NOT in PATH AND venv NOT found: !PY!
        echo       Install uv: pip install uv
        echo       Or create venv: cd backend ^&^& uv venv .venv ^&^& uv pip install -e .
        pause
        exit /b 1
    )
    set RUNNER="!PY!"
)

echo.
echo ============================================================
echo   Service Control  -  stop / status / doctor
echo ============================================================
echo.

%RUNNER% "%SCRIPT_DIR%start_local.py" %*

set RC=%ERRORLEVEL%
if not %RC% == 0 (
    echo.
    echo [WARN] exit code %RC%
)

endlocal & exit /b %RC%

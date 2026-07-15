@echo off
REM ============================================================
REM  Stop all local services (thin wrapper for start_local.py stop)
REM ============================================================
chcp 65001 >nul
setlocal
set SCRIPT_DIR=%~dp0
set PY=%SCRIPT_DIR%..\backend\.venv\Scripts\python.exe
"%PY%" "%SCRIPT_DIR%start_local.py" stop
endlocal

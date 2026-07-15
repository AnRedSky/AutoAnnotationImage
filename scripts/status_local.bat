@echo off
REM ============================================================
REM  Show local service status
REM ============================================================
chcp 65001 >nul
setlocal
set SCRIPT_DIR=%~dp0
set PY=%SCRIPT_DIR%..\backend\.venv\Scripts\python.exe
"%PY%" "%SCRIPT_DIR%start_local.py" status
endlocal

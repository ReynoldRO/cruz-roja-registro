@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "MODO_PRUEBA=0"
set "HOST=0.0.0.0"
where py >nul 2>&1
if %ERRORLEVEL% EQU 0 (
  py main.py
) else (
  python main.py
)
pause

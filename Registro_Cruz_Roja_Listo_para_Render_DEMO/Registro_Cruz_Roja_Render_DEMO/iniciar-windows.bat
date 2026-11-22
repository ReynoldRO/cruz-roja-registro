@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "MODO_PRUEBA=1"
set "HOST=0.0.0.0"
echo =====================================================
echo   CRUZ ROJA - ACCESO DIRECTO PARA CELULAR (DEMO)
echo   En esta PC: http://localhost:8765
echo   En el celular: http://IP-DE-TU-PC:8765
echo   Administracion: http://localhost:8765/admin
echo =====================================================
echo.
where py >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    py -m pip install -r requirements.txt
    py main.py
) else (
    python -m pip install -r requirements.txt
    python main.py
)
pause

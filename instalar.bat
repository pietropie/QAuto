@echo off
echo ============================================================
echo   QA Automation - Instalando dependencias
echo ============================================================
echo.

:: Verifica Python
python --version >nul 2>&1
if errorlevel 1 (
    echo ERRO: Python nao encontrado. Instale em https://python.org
    pause
    exit /b 1
)

:: Instala dependencias
echo Instalando pacotes Python...
pip install -r requirements.txt

:: Instala browsers do Playwright
echo.
echo Instalando Chromium (Playwright)...
playwright install chromium

echo.
echo ============================================================
echo   Instalacao concluida!
echo.
echo   Proximos passos:
echo     1. Edite o arquivo config.yaml
echo     2. Execute: python main.py
echo ============================================================
pause

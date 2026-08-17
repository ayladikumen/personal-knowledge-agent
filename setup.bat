@echo off
setlocal
cd /d "%~dp0"

echo ============================================================
echo   Personal Knowledge Agent - setup
echo ============================================================
echo.

set "PY=%~dp0venv\Scripts\python.exe"

if not exist "%PY%" (
    echo [1/5] Creating virtual environment...
    python -m venv venv
    if errorlevel 1 (
        echo.
        echo   ERROR: could not create the venv. Is Python on your PATH?
        goto :end
    )
) else (
    echo [1/5] Virtual environment found.
)

echo.
echo [2/5] Removing packages this project no longer uses...
"%PY%" -m pip uninstall -y chromadb chroma-hnswlib google-generativeai python-telegram-bot >nul 2>&1
echo       done.

echo.
echo [3/5] Installing dependencies...
"%PY%" -m pip install --upgrade pip --quiet
"%PY%" -m pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo   ERROR: dependency install failed.
    goto :end
)

if not exist "vault" mkdir "vault"
if not exist "data"  mkdir "data"

echo.
echo [4/5] Registering the MCP server with Claude Desktop...
"%PY%" tools\configure_claude.py
if errorlevel 1 (
    echo.
    echo   ERROR: could not update the Claude Desktop config.
    goto :end
)

echo.
echo [5/5] Running setup checks...
"%PY%" tools\check_setup.py

echo.
echo ============================================================
echo   Done. Fully quit Claude Desktop (system tray - Quit),
echo   then reopen it to load the server.
echo ============================================================

:end
echo.
pause
endlocal

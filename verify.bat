@echo off
setlocal
cd /d "%~dp0"
set "PY=%~dp0venv\Scripts\python.exe"
set "LOG=%~dp0setup-log.txt"

echo Running verification, writing to setup-log.txt ...

> "%LOG%" 2>&1 (
    echo ==================== SETUP VERIFICATION ====================
    echo.
    echo ---- Installed packages ----
    "%PY%" -m pip list
    echo.
    echo ---- Claude Desktop config ----
    "%PY%" tools\show_claude_config.py
    echo.
    echo ---- Setup checks ----
    "%PY%" tools\check_setup.py
    echo.
    echo ---- MCP server boot test ----
    "%PY%" -c "import sys, asyncio; sys.path.insert(0,'src'); import mcp_server; print('mcp_server imported OK; tools:', ', '.join(t.name for t in asyncio.run(mcp_server.mcp.list_tools())))"
    echo.
    echo ==================== END ====================
)

echo Done.
timeout /t 3 >nul
endlocal

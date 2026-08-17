@echo off
cd /d "%~dp0"
echo Registering the MCP server with Claude Desktop...
"%~dp0venv\Scripts\python.exe" "%~dp0tools\configure_claude.py" > "%~dp0diag-log.txt" 2>&1
"%~dp0venv\Scripts\python.exe" "%~dp0tools\show_claude_config.py" >> "%~dp0diag-log.txt" 2>&1
type "%~dp0diag-log.txt"
echo.
echo Now fully quit Claude Desktop from the tray icon, then reopen it.
pause

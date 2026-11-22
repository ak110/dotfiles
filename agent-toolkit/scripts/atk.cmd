@echo off
setlocal
for /f "delims=" %%A in ('cd /d "%~dp0.." ^& cd') do set "SCRIPT_DIR=%%A"
set "SCRIPT=%SCRIPT_DIR%\agent_toolkit\atk.py"

uv run --no-cache --no-config --no-project --python 3.15 python "%SCRIPT_DIR%\scripts\launch.py" --prepare-if-missing "%SCRIPT%" %*
set "STATUS=%ERRORLEVEL%"

endlocal & exit /b %STATUS%

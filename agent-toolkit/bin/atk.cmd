@echo off
setlocal
for /f "delims=" %%A in ('cd /d "%~dp0.." ^& cd') do set SCRIPT_DIR=%%A
uv run --no-project --script "%SCRIPT_DIR%\scripts\atk.py" %*

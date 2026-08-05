@echo off
setlocal
for /f "delims=" %%A in ('cd /d "%~dp0.." ^& cd') do set "PLUGIN_ROOT=%%A"
set "HELPER=%PLUGIN_ROOT%\scripts\_managed_temp.py"

uv run --no-project --script "%HELPER%" %*
set "STATUS=%ERRORLEVEL%"
endlocal & exit /b %STATUS%

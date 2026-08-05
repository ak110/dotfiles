@echo off
setlocal
for /f "delims=" %%A in ('cd /d "%~dp0.." ^& cd') do set SCRIPT_DIR=%%A
set "UV=%USERPROFILE%\.local\bin\uv.exe"
if not exist "%UV%" (
    echo uv was not found. Install uv with the official installer. 1>&2
    exit /b 127
)
"%UV%" self update || exit /b 1
"%UV%" run --no-project --script "%SCRIPT_DIR%\scripts\update_dotfiles.py" %*

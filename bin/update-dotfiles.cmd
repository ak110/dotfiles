@echo off
setlocal
for /f "delims=" %%A in ('cd /d "%~dp0.." ^& cd') do set SCRIPT_DIR=%%A
set "UV=%USERPROFILE%\.local\bin\uv.exe"
if not exist "%UV%" (
    echo uv was not found. Install uv with the official installer. 1>&2
    exit /b 127
)
set "UV_SELF_UPDATE_FAILED=0"
"%UV%" self update
if errorlevel 1 set "UV_SELF_UPDATE_FAILED=1"
"%UV%" run --no-project --script "%SCRIPT_DIR%\scripts\update_dotfiles.py" %*
set "UPDATE_DOTFILES_EXIT=%ERRORLEVEL%"
if "%UV_SELF_UPDATE_FAILED%"=="1" echo uvの自己更新に失敗しました。既存のuvでdotfiles更新を実行し、次回のupdate-dotfiles起動時に自己更新を再試行します。 1>&2
exit /b %UPDATE_DOTFILES_EXIT%

@echo off
setlocal
for /f "delims=" %%A in ('cd /d "%~dp0.." ^& cd') do set SCRIPT_DIR=%%A
set "UV=%USERPROFILE%\.local\bin\uv.exe"
if not exist "%UV%" (
    echo uv was not found. Install uv with the official installer. 1>&2
    exit /b 127
)
set "UV_SELF_UPDATE_FAILED=0"
if not "%AGENT_TOOLKIT_PROCESS_LOOP_SESSION%"=="1" (
    "%UV%" self update
    if errorlevel 1 set "UV_SELF_UPDATE_FAILED=1"
)
"%UV%" run --no-project --script "%SCRIPT_DIR%\scripts\update_dotfiles.py" %*
set "UPDATE_DOTFILES_EXIT=%ERRORLEVEL%"
set "UV_SELF_UPDATE_WARNING=uvの自己更新に失敗しました。既存のuvでdotfiles更新を実行し、次回のupdate-dotfiles起動時に自己更新を再試行します。"
rem CP932のbatch文字列を、出力先に応じた文字コードで標準エラー出力へ渡す。
if "%UV_SELF_UPDATE_FAILED%"=="1" powershell.exe -NoLogo -NoProfile -Command "$message=$env:UV_SELF_UPDATE_WARNING; if ([Console]::IsErrorRedirected) { $writer=[System.IO.StreamWriter]::new([Console]::OpenStandardError(), [System.Text.UTF8Encoding]::new($false)); try { $writer.WriteLine($message) } finally { $writer.Dispose() } } else { [Console]::Error.WriteLine($message) }"
exit /b %UPDATE_DOTFILES_EXIT%

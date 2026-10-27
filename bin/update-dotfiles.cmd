@echo off
setlocal DisableDelayedExpansion
set "SCRIPT_DIR=%~dp0.."
set "UV=%USERPROFILE%\.local\bin\uv.exe"
if not exist "%UV%" goto missing_uv
set "UV_SELF_UPDATE_FAILED=0"
if not "%~1"=="logs" if not "%AGENT_TOOLKIT_PROCESS_LOOP_SESSION%"=="1" call :update_uv
set "DOTFILES_PRUNE_OUTSIDE_UV=1"
set "PRUNE_REQUESTED=0"
(
"%UV%" run --no-project --script "%SCRIPT_DIR%\libexec\update_dotfiles.py" %*
rem ----------------------------------------------------------------------------
rem 123456789012345
rem x
call set "UPDATE_DOTFILES_EXIT=%%ERRORLEVEL%%"
for %%A in (%*) do if "%%~A"=="--prune" set "PRUNE_REQUESTED=1"
for %%A in (%*) do for %%H in (-h --help) do if "%%~A"=="%%H" set "PRUNE_REQUESTED=0"
rem 引数とパス内の!を保つため、uv実行と引数走査の後で遅延展開を有効にする。
setlocal EnableDelayedExpansion
if "!UPDATE_DOTFILES_EXIT!"=="0" if "%DOTFILES_PRUNE_OUTSIDE_UV%"=="1" if "!PRUNE_REQUESTED!"=="1" (
    "!USERPROFILE!\.local\bin\dotfiles-post-apply.exe" --prune-only
    set "UPDATE_DOTFILES_EXIT=!ERRORLEVEL!"
)
rem 上のコメント末尾は旧legacy・logs版の再開位置。byte位置はランチャーテストで検証する。
set "UV_SELF_UPDATE_WARNING=uvの自己更新に失敗しました。既存のuvでdotfiles更新を実行し、次回のupdate-dotfiles起動時に自己更新を再試行します。対応不要（dotfilesの更新処理は継続しました）。"
rem CP932のbatch文字列を、出力先に応じた文字コードで標準エラー出力へ渡す。
if "%UV_SELF_UPDATE_FAILED%"=="1" powershell.exe -NoLogo -NoProfile -Command "$message=$env:UV_SELF_UPDATE_WARNING; if ([Console]::IsErrorRedirected) { $writer=[System.IO.StreamWriter]::new([Console]::OpenStandardError(), [System.Text.UTF8Encoding]::new($false)); try { $writer.WriteLine($message) } finally { $writer.Dispose() } } else { [Console]::Error.WriteLine($message) }"
exit /b !UPDATE_DOTFILES_EXIT!
)
:missing_uv
    echo uvが見つかりません。次の操作: https://docs.astral.sh/uv/getting-started/installation/ の公式手順でuvを導入し、update-dotfilesを再実行してください。 1>&2
    exit /b 127

:update_uv
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%\libexec\update_uv.ps1" "%UV%"
if errorlevel 1 set "UV_SELF_UPDATE_FAILED=1"
exit /b 0

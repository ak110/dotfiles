@echo off
setlocal DisableDelayedExpansion
set "SCRIPT_DIR=%~dp0.."
set "UV=%USERPROFILE%\.local\bin\uv.exe"
rem bootstrap checks uv--------------------
goto start
:run
(
"%UV%" %UPDATE_RUN_ARGS% "%SCRIPT_DIR%\libexec\%UPDATE_SCRIPT_PY%" %*
rem ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
rem 123456789012345
rem x
call set "UPDATE_DOTFILES_EXIT=%%ERRORLEVEL%%"
for %%A in (%*) do if "%%~A"=="--prune" set "PRUNE_REQUESTED=1"
for %%A in (%*) do for %%H in (-h --help) do if "%%~A"=="%%H" set "PRUNE_REQUESTED=0"
rem 引数とパス内の!を保つため、uv実行と引数走査の後で遅延展開を有効にする。
setlocal EnableDelayedExpansion
rem 続行した失敗だけで本体を終えた場合（終了コード1）も、印があれば整理と事前構築へ進む。
set "RUN_PRUNE=0"
if "!UPDATE_DOTFILES_EXIT!"=="0" set "RUN_PRUNE=1"
if defined DOTFILES_PRUNE_READY_FILE if exist "!DOTFILES_PRUNE_READY_FILE!" set "RUN_PRUNE=1"
if "!RUN_PRUNE!"=="1" if "%DOTFILES_PRUNE_OUTSIDE_UV%"=="1" if "!PRUNE_REQUESTED!"=="1" (
    "!USERPROFILE!\.local\bin\dotfiles-post-apply.exe" --prune-only-if-minimal %*
    if "!UPDATE_DOTFILES_EXIT!"=="0" set "UPDATE_DOTFILES_EXIT=!ERRORLEVEL!"
)
if defined DOTFILES_PRUNE_READY_FILE if exist "!DOTFILES_PRUNE_READY_FILE!" del /q "!DOTFILES_PRUNE_READY_FILE!"
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
set "UPDATE_DOTFILES_BOOTSTRAP_REPORT=%LOCALAPPDATA%\agent-toolkit\update-bootstrap-%RANDOM%-%RANDOM%.json"
for %%A in (%*) do for %%H in (-h --help) do if "%%~A"=="%%H" exit /b 0
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%\libexec\update_bootstrap.ps1" "%UV%"
exit /b %ERRORLEVEL%

:start
if "%~1"=="logs" goto normal
if "%~1"=="job" goto job_route
if "%AGENT_TOOLKIT_PROCESS_LOOP_SESSION%"=="1" goto dispatch
call :update_uv %*
if errorlevel 1 exit /b %ERRORLEVEL%
:dispatch
if defined CODEX_THREAD_ID goto job_route
if "%~1"=="job" goto job_route
:normal
set "UPDATE_RUN_ARGS=run --no-project --script"
set "UPDATE_SCRIPT_PY=update_dotfiles.py"
set "UV_SELF_UPDATE_FAILED=0"
set "DOTFILES_PRUNE_OUTSIDE_UV=1"
rem 前回の更新が始めたuvの全消去は最長600秒キャッシュを排他するため、uvの既定の待機上限（300秒）より長く待つ。
if not defined UV_LOCK_TIMEOUT set "UV_LOCK_TIMEOUT=900"
set "DOTFILES_PRUNE_READY_FILE=%LOCALAPPDATA%\agent-toolkit\update-prune-ready-%RANDOM%-%RANDOM%"
set "PRUNE_REQUESTED=1"
if "%~1"=="logs" set "PRUNE_REQUESTED=0"
if "%~1"=="logs" goto run
for %%A in (%*) do for %%H in (-h --help) do if "%%~A"=="%%H" goto run
powershell.exe -NoLogo -NoProfile -Command "$message='更新本体の起動: uvキャッシュの整理中は待つ場合があります。Ctrl+Cで中断できます。'; if ([Console]::IsErrorRedirected) { $writer=[IO.StreamWriter]::new([Console]::OpenStandardError(), [Text.UTF8Encoding]::new($false)); try { $writer.WriteLine($message) } finally { $writer.Dispose() } } else { [Console]::Error.WriteLine($message) }"
set "UPDATE_RUN_ARGS=run --no-cache --quiet --no-project --script"
set "UPDATE_SCRIPT_PY=update_script_run.py"
goto run

:job_route
for %%A in (%*) do for %%H in (-h --help) do if "%%~A"=="%%H" goto normal
set "UPDATE_PYTHON="
for /f "delims=" %%P in ('powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%\libexec\update_bootstrap.ps1" "%UV%" -PrepareOnly -PrintPython') do set "UPDATE_PYTHON=%%P"
if not defined UPDATE_PYTHON exit /b 1
(
"%UPDATE_PYTHON%" "%SCRIPT_DIR%\libexec\update_dotfiles_job.py" %*
call exit /b %%ERRORLEVEL%%
)

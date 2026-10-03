@echo off
setlocal EnableExtensions DisableDelayedExpansion
rem 同じGitリポジトリのcwdから呼んだ場合だけ、対象worktreeの入口へ移る。
for %%I in ("%~dp0..") do set "INSTALL_ROOT=%%~fI"
set "INSTALL_COMMON="
for /f "delims=" %%I in ('git -C "%INSTALL_ROOT%" rev-parse --path-format=absolute --git-common-dir') do set "INSTALL_COMMON=%%I"
if not defined INSTALL_COMMON (
    echo atkの所属リポジトリを解決できません。Gitの導入とdotfilesの配置を確かめて再実行してください。 1>&2
    exit /b 127
)
set "TARGET_ROOT=%INSTALL_ROOT%"
set "CWD_COMMON="
for /f "delims=" %%I in ('git rev-parse --path-format=absolute --git-common-dir 2^>nul') do set "CWD_COMMON=%%I"
if /i not "%INSTALL_COMMON%"=="%CWD_COMMON%" goto dispatch
for /f "delims=" %%I in ('git rev-parse --show-toplevel') do set "TARGET_ROOT=%%I"
:dispatch
set "ATK_ENTRY=%TARGET_ROOT%\agent-toolkit\bin\atk.cmd"
if not exist "%ATK_ENTRY%" (
    echo atkの起動先がありません: %ATK_ENTRY% 1>&2
    echo 次の操作: 対象worktreeのagent-toolkit\bin\atk.cmdを復元してから再実行する。 1>&2
    exit /b 127
)
rem callによる引数の再展開を避け、常駐制御と終了コードを子のバッチへ委ねる。
"%ATK_ENTRY%" %*

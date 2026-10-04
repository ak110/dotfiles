@echo off
setlocal EnableExtensions DisableDelayedExpansion
rem 同じGit管理下のcwdから呼んだ場合だけ、対象worktreeの入口へ移る。
for %%I in ("%~dp0..") do set "INSTALL_ROOT=%%~fI"
set "INSTALL_COMMON="
pushd "%INSTALL_ROOT%" || exit /b 127
for /f "delims=" %%I in ('git rev-parse --git-common-dir') do for %%J in ("%%I") do set "INSTALL_COMMON=%%~fJ"
popd
if not defined INSTALL_COMMON (
    echo atkの所属Git管理先を解決できません。Gitの導入とdotfilesの配置を確かめて再実行してください。 1>&2
    exit /b 127
)
set "TARGET_ROOT=%INSTALL_ROOT%"
set "CWD_COMMON="
for /f "delims=" %%I in ('git rev-parse --git-common-dir 2^>nul') do for %%J in ("%%I") do set "CWD_COMMON=%%~fJ"
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

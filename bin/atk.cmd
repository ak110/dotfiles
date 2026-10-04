@echo off
setlocal EnableExtensions DisableDelayedExpansion
rem 同じGit管理下のcwdから呼んだ場合だけ、対象worktreeの入口へ移る。
for %%I in ("%~dp0..") do set "INSTALL_ROOT=%%~fI"
set "INSTALL_COMMON="
pushd "%INSTALL_ROOT%" || exit /b 127
for /f "delims=" %%I in ('git rev-parse --git-common-dir') do for %%J in ("%%I") do set "INSTALL_COMMON=%%~fJ"
popd
if not defined INSTALL_COMMON (
    rem 診断文をUTF-8で出し、batch原本と実行環境の文字コード差を避ける。
    set "ATK_ERROR_B64=YXRr44Gu5omA5bGeR2l0566h55CG5YWI44KS6Kej5rG644Gn44GN44G+44Gb44KT44CCR2l044Gu5bCO5YWl44GoZG90ZmlsZXPjga7phY3nva7jgpLnorrjgYvjgoHjgablho3lrp/ooYzjgZfjgabjgY/jgaDjgZXjgYTjgII="
    set "ATK_ERROR_SUFFIX="
    set "ATK_NEXT_B64="
    call :write_error
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
    set "ATK_ERROR_B64=YXRr44Gu6LW35YuV5YWI44GM44GC44KK44G+44Gb44KTOiA="
    set "ATK_ERROR_SUFFIX=%ATK_ENTRY%"
    set "ATK_NEXT_B64=5qyh44Gu5pON5L2cOiDlr77osaF3b3JrdHJlZeOBrmFnZW50LXRvb2xraXRcYmluXGF0ay5jbWTjgpLlvqnlhYPjgZfjgabjgYvjgonlho3lrp/ooYzjgZnjgovjgII="
    call :write_error
    exit /b 127
)
rem callによる引数の再展開を避け、常駐制御と終了コードを子のバッチへ委ねる。
"%ATK_ENTRY%" %*
exit /b %ERRORLEVEL%

:write_error
powershell.exe -NoLogo -NoProfile -NonInteractive -Command "$u=[Text.UTF8Encoding]::new($false);$m=$u.GetString([Convert]::FromBase64String($env:ATK_ERROR_B64))+$env:ATK_ERROR_SUFFIX;$n='';if ($env:ATK_NEXT_B64) {$n=$u.GetString([Convert]::FromBase64String($env:ATK_NEXT_B64))};if ([Console]::IsErrorRedirected) {$w=[IO.StreamWriter]::new([Console]::OpenStandardError(),$u);try {$w.WriteLine($m);if ($n) {$w.WriteLine($n)}} finally {$w.Dispose()}} else {[Console]::Error.WriteLine($m);if ($n) {[Console]::Error.WriteLine($n)}}"
exit /b %ERRORLEVEL%

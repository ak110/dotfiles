@echo off
rem NOTE: 対応するLinux版 → bin/sshr
rem ssh自動再接続ラッパー。
rem - 引数はssh本体へ完全透過する
rem - exit 0で終了 → ループ脱出
rem - その他のexit code → メッセージを表示して即座に再実行
rem - Ctrl+Cはcmdの既定動作 (Terminate batch job? Y/N) に委ねる
setlocal

:loop
ssh %*
set ec=%ERRORLEVEL%
if "%ec%"=="0" exit /b 0
echo [sshr] 切断されました (exit=%ec%)。再接続します... (Ctrl+C で終了) 1>&2
goto loop

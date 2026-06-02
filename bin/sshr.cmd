@echo off
rem NOTE: 対応するLinux版 → bin/sshr
rem ssh自動再接続ラッパー。
rem - 引数はssh本体へ完全透過する
rem - exit 0で終了 → ループ脱出
rem - その他のexit code → 端末状態を初期化してからメッセージを表示し再実行
rem - Ctrl+Cはcmdの既定動作 (Terminate batch job? Y/N) に委ねる
rem - ssh.exeは出力コードページをUTF-8(65001)へ変更し復元しない(Win32-OpenSSH #2027)。
rem   起動時の値を保存し、ssh実行後に復元してから日本語メッセージを表示する
setlocal

rem 起動時(ssh実行前)の出力コードページ番号を保存する(chcp出力 "... : N" の数値部分)
for /f "tokens=2 delims=:" %%a in ('chcp') do set "origcp=%%a"
set "origcp=%origcp: =%"

:loop
ssh %*
set ec=%ERRORLEVEL%
rem ssh.exeが変更したコードページを起動時の値へ復元する
chcp %origcp% >nul
if "%ec%"=="0" exit /b 0
powershell -NoProfile -Command "$e=[char]27; @('[?1000l','[?1002l','[?1003l','[?1004l','[?1006l','[?1015l','[?2004l','[?1049l','[0m','[?25h') | %% { [Console]::Error.Write($e+$_) }"
echo [sshr] 切断されました (exit=%ec%)。再接続します... (Ctrl+C で終了) 1>&2
goto loop

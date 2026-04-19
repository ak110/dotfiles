@echo off
setlocal

rem WindowsからSSH先Linuxの~/.claude/plans/*.mdをブラウザで閲覧するラッパー。
rem 使い方: remote-plans USER@HOST [PORT]
rem   USER@HOST: SSH接続先（~/.ssh/configのホスト別名でも可）
rem   PORT:      ローカルとリモートで共通に使うポート（既定 8765）
rem 終了するにはこのウィンドウでCtrl+Cを押すか、ウィンドウを閉じる。
rem 対応するLinux版: なし（リモート側のビューア本体は claude-plans-viewer として配布）。

if "%~1"=="" (
    echo usage: remote-plans USER@HOST [PORT]
    exit /b 1
)

set "TARGET=%~1"
set "PORT=%~2"
if "%PORT%"=="" set "PORT=8765"

set "URL=http://127.0.0.1:%PORT%/"

rem サーバー起動を待ってからブラウザを開く。別ウィンドウでバックグラウンド実行する。
start "remote-plans browser" /min cmd /c "timeout /t 3 /nobreak >nul && start %URL%"

rem SSHトンネルを張りつつ、リモート側のビューアを前景で起動する。
ssh -L %PORT%:127.0.0.1:%PORT% %TARGET% "~/.local/bin/claude-plans-viewer --host 127.0.0.1 --port %PORT%"

endlocal

@echo off
setlocal

rem WindowsからSSH先Linuxの~/.claude/plans/*.mdをブラウザで閲覧するラッパー。
rem 使い方: remote-plans USER@HOST [PORT]
rem   USER@HOST: SSH接続先（~/.ssh/configのホスト別名でも可）
rem   PORT:      ローカルポートの開始候補値（既定 28766）。bindに失敗した場合は連番で最大50個まで自動探索する
rem 終了するにはこのウィンドウでCtrl+Cを押すか、ウィンドウを閉じる。
rem 対応するLinux版: なし（リモート側のビューア本体は claude-plans-viewer として配布）。
rem
rem ポート自動選択の背景:
rem   Windows側で127.0.0.1へのTCP bindがPermission denied等で失敗する環境がある
rem  （VSCodeの自動ポート転送など）。
rem   そのためssh起動前にPowerShellでTcpListener bindを試行し、成功した最初のポートを採用する。
rem   probeとssh起動の間に他プロセスがポートを奪う競合に備え、-o ExitOnForwardFailure=yes を指定する。

if "%~1"=="" (
    echo usage: remote-plans USER@HOST [PORT]
    exit /b 1
)

set "TARGET=%~1"
set "START_PORT=%~2"
if "%START_PORT%"=="" set "START_PORT=28766"

set "PORT="
for /f "usebackq tokens=*" %%p in (`powershell -NoProfile -Command "$s=%START_PORT%; for($i=0;$i -lt 50;$i++){$p=$s+$i; try{$l=[System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback,$p); $l.Start(); $l.Stop(); Write-Output $p; exit 0}catch{}}; exit 1"`) do set "PORT=%%p"

if not defined PORT (
    echo error: no local port available in %START_PORT%..%START_PORT%+49 for bind on 127.0.0.1
    exit /b 1
)

if "%PORT%"=="%START_PORT%" (
    echo using port %PORT%
) else (
    echo port %START_PORT% unavailable, using %PORT%
)

rem SSHトンネルを張りつつ、リモート側のビューアを前景で起動する。
ssh -t -o ExitOnForwardFailure=yes -L %PORT%:127.0.0.1:%PORT% %TARGET% "~/.local/bin/claude-plans-viewer --host 127.0.0.1 --port %PORT%"

endlocal

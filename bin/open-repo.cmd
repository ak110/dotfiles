@echo off
rem NOTE: 対応するLinux版 → bin/open-repo
rem リモートURLのホスト部分でGitHub/GitLabを判定し、対応するCLIでブラウザを開く。
rem - GitHub (github.com): gh browse
rem - GitLab (gitlab.com): glab repo view --web
rem - それ以外: エラーを出力して終了
rem - 引数はすべて委譲先CLIへ完全透過する
setlocal

for /f "usebackq delims=" %%u in (`git remote get-url origin`) do set "OPEN_REPO_URL=%%u"

if not defined OPEN_REPO_URL (
    echo open-repo: git remote get-url origin が失敗しました 1>&2
    exit /b 1
)

set "host="
for /f "usebackq delims=" %%h in (`powershell -NoProfile -Command "$u=$env:OPEN_REPO_URL; if($u -match '^git@([^:]+):'){$Matches[1]; exit 0}; try{$uri=[Uri]$u; if($uri.Scheme -eq 'https'){$uri.Host; exit 0}}catch{}; exit 1"`) do set "host=%%h"

if not defined host (
    echo open-repo: 未対応のURL形式です 1>&2
    exit /b 1
)

if /i "%host%"=="github.com" (
    gh browse %*
    exit /b %ERRORLEVEL%
)

if /i "%host%"=="gitlab.com" (
    glab repo view --web %*
    exit /b %ERRORLEVEL%
)

echo open-repo: 未対応のホストです: %host% 1>&2
exit /b 1

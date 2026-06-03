@echo off
rem NOTE: 対応するLinux版 → bin/open-repo
rem リモートURLのホスト部分でGitHub/GitLabを判定し、対応するCLIでブラウザを開く。
rem - GitHub (github.com): gh browse
rem - GitLab (gitlab.com): glab repo view --web
rem - それ以外: エラーを出力して終了
rem - 引数はすべて委譲先CLIへ完全透過する
setlocal

for /f "usebackq delims=" %%u in (`git remote get-url origin`) do set "url=%%u"

if not defined url (
    echo open-repo: git remote get-url origin が失敗しました 1>&2
    exit /b 1
)

set "host="
echo %url% | findstr /i "^git@" >nul 2>&1
if %ERRORLEVEL%==0 (
    for /f "tokens=2 delims=@:" %%h in ("%url%") do set "host=%%h"
)
echo %url% | findstr /i "^https://" >nul 2>&1
if %ERRORLEVEL%==0 (
    set "tmp=%url:https://=%"
    for /f "tokens=1 delims=/" %%h in ("%tmp%") do set "host=%%h"
)

if not defined host (
    echo open-repo: 未対応のURL形式です: %url% 1>&2
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

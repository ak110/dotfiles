@echo off
REM dotfiles を最新化する。
REM 対応する Linux 版 → bin/executable_update-dotfiles
REM
REM git pull → init → status (apply 予定のみ表示) → silent apply の 4 段で実行する。
REM step 3 で「これから更新されるファイル」を一覧表示してから step 4 で apply を流す構成のため、
REM `chezmoi update` (= 内部で git pull + apply 一括) ではなく `chezmoi git pull` を使っている。

echo === [1/4] git pull ===
chezmoi git --source "%USERPROFILE%\dotfiles" -- pull --ff-only
if errorlevel 1 exit /b %errorlevel%

echo === [2/4] chezmoi init (テンプレート再展開) ===
chezmoi init --source "%USERPROFILE%\dotfiles"
if errorlevel 1 exit /b %errorlevel%

echo === [3/4] chezmoi status (apply 予定のファイル) ===
REM `chezmoi status` は 2 列構成。
REM   1 列目 = 前回 chezmoi が書いた状態 vs 現在の destination 実ファイル
REM   2 列目 = 現在の実ファイル vs target state (= これから apply で起きる変更)
REM 知りたいのは「これから更新されるファイル」だけなので 2 列目が A/D/M の行のみ残す。
REM スクリプト (R) は毎回出やすくノイズになるため -x scripts で除外する。
REM findstr はマッチなしで errorlevel 1 を返すので、後段を成功扱いにするため `ver >nul` で握りつぶす。
chezmoi status -x scripts | findstr /R "^.[ADM]"
ver >nul

echo === [4/4] chezmoi apply (post-apply 実行) ===
chezmoi apply
if errorlevel 1 exit /b %errorlevel%

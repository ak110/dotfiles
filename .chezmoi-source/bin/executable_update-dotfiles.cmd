@echo off
chezmoi update --verbose --source "%USERPROFILE%\dotfiles"
REM 設定テンプレートの変更を反映（chezmoi updateでは再生成されない）
chezmoi init --source "%USERPROFILE%\dotfiles"
REM Claude Code settings.json を管理対象設定とマージ
REM NOTE: 対応するLinux版 → bin/executable_update-dotfiles
if exist "%USERPROFILE%\.local\bin\update-claude-settings.exe" "%USERPROFILE%\.local\bin\update-claude-settings.exe"

@echo off
chezmoi update --verbose --source "%USERPROFILE%\dotfiles"
REM 設定テンプレートの変更を反映（chezmoi updateでは再生成されない）
chezmoi init --source "%USERPROFILE%\dotfiles"

@echo off
chezmoi update --source "%USERPROFILE%\dotfiles"
REM Regenerate config from template (chezmoi update does not do this)
chezmoi init --source "%USERPROFILE%\dotfiles"

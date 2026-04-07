@echo off
REM Update dotfiles.
REM Paired with bin/executable_update-dotfiles (Linux).
REM
REM chezmoi execution order:
REM   1. update - git pull + apply (no template re-render)
REM   2. init   - re-render templates (run_after_* does NOT fire here)
REM   3. apply  - reflect re-rendered source state to destination
REM               (run_after_post-apply fires here for final post-apply)

echo === [1/3] chezmoi update ===
chezmoi update --verbose --source "%USERPROFILE%\dotfiles"

echo === [2/3] chezmoi init ===
chezmoi init --source "%USERPROFILE%\dotfiles"

echo === [3/3] chezmoi apply (post-apply) ===
chezmoi apply --verbose

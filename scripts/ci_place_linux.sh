#!/usr/bin/env bash
set -euo pipefail
mv dotfiles ~/dotfiles
echo "DOTFILES_DIR=$HOME/dotfiles" >>"$GITHUB_ENV"

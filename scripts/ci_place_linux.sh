#!/usr/bin/env bash
set -euo pipefail
# post工程もworkspace内のローカルaction定義を再読するため、元のcheckoutを保持する。
cp -a -- "$GITHUB_WORKSPACE/dotfiles" "$HOME/dotfiles"
echo "DOTFILES_DIR=$HOME/dotfiles" >>"$GITHUB_ENV"

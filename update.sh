#!/bin/bash
set -eux
DOT_DIR=~/dotfiles
cd $DOT_DIR
git pull --no-recurse-submodules

if [ -f "$DOT_DIR/.chezmoiignore" ]; then
    # chezmoi形式のリポジトリを検出 → 移行処理
    # deploy.shに移行ロジックがあるのでそちらに委譲
    ./deploy.sh
else
    # 旧形式 → 従来通り
    git submodule update --init --recursive
    ./deploy.sh
fi

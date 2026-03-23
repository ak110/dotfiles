#!/bin/bash
set -eux
DOT_DIR=~/dotfiles
cd $DOT_DIR
git pull --no-recurse-submodules

if [ -f "$DOT_DIR/.chezmoiignore" ]; then
    # chezmoi形式のリポジトリを検出 → 移行処理
    # 1. chezmoiインストール
    if ! command -v chezmoi &> /dev/null; then
        sh -c "$(curl -fsLS get.chezmoi.io)" -- -b ~/.local/bin
        export PATH="$HOME/.local/bin:$PATH"
    fi
    # 2. ~/dotfiles/ を指すシンボリックリンクを解除し、実体をホームへ移動
    for f in ~/.[!.]*; do
        [ -L "$f" ] || continue
        target="$(readlink "$f")"
        case "$target" in "$DOT_DIR"/*|"$HOME/dotfiles/"*) ;; *) continue ;; esac
        if [ -d "$target" ]; then
            rm "$f"
            mv "$target" "$f"
        else
            rm "$f"
        fi
    done
    # 3. chezmoi初期化＆適用
    chezmoi init --source "$DOT_DIR" --apply
    echo "chezmoi migration complete."
else
    # 旧形式 → 従来通り
    git submodule update --init --recursive
    ./deploy.sh
fi

#!/bin/bash
# 移行スクリプト: 旧update.shのgit pull後に呼ばれる。
# chezmoiをインストールし、シンボリックリンクを実体化して適用する。
set -eux
DOT_DIR=~/dotfiles

# chezmoiインストール
if ! command -v chezmoi &> /dev/null; then
    sh -c "$(curl -fsLS get.chezmoi.io)" -- -b ~/.local/bin
    export PATH="$HOME/.local/bin:$PATH"
fi

# ~/dotfiles/ を指すシンボリックリンクを解除し、実体をホームへ移動
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

# chezmoi初期化＆適用
chezmoi init --source "$DOT_DIR" --apply
echo "chezmoi移行完了。シェルを再起動してください。"

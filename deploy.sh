#!/bin/bash
# 移行スクリプト: 旧update.shのgit pull後に呼ばれる。
# chezmoiをインストールし、シンボリックリンクを実体化して適用する。
set -eux
DOT_DIR=~/dotfiles

# chezmoiインストール
if ! command -v chezmoi &> /dev/null; then
    sh -c "$(curl -fsSL get.chezmoi.io)" -- -b ~/.local/bin
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

# chezmoi管理対象の既存ファイルをバックアップ
backup_existing_dotfiles() {
    local source_dir="$1"
    local backup_dir="$HOME/.dotfiles-backup/$(date +%Y%m%d-%H%M%S)"
    local count=0

    while IFS= read -r target; do
        local src="$HOME/$target"
        # ファイルとシンボリックリンクのみ対象（ディレクトリはスキップ）
        [ -f "$src" ] || [ -L "$src" ] || continue
        local dest_dir="$backup_dir/$(dirname "$target")"
        mkdir -p "$dest_dir"
        cp -a "$src" "$dest_dir/"
        count=$((count + 1))
    done < <(chezmoi managed --source "$source_dir")

    if [ "$count" -gt 0 ]; then
        echo "Backed up $count existing files to: $backup_dir"
    fi
}

# chezmoi初期化＆適用
backup_existing_dotfiles "$DOT_DIR"
chezmoi init --verbose --source "$DOT_DIR" --apply
echo "chezmoi移行完了。シェルを再起動してください。"

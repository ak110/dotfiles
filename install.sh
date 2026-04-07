#!/bin/bash
set -eu

# 前提条件チェック (README の「前提条件(要インストール)」セクション参照)
missing=()
command -v git >/dev/null 2>&1 || missing+=(git)
command -v uv >/dev/null 2>&1 || missing+=(uv)
if [ ${#missing[@]} -gt 0 ]; then
    echo "Error: 前提条件が未インストールです: ${missing[*]}" >&2
    echo "README の「前提条件(要インストール)」セクションを参照してインストールしてください:" >&2
    echo "  https://github.com/ak110/dotfiles#前提条件要インストール" >&2
    exit 1
fi

set -x
# 既に chezmoi が入っていればダウンロードをスキップ (冪等性とテスト用途)
export PATH="$HOME/.local/bin:$PATH"
if ! command -v chezmoi >/dev/null 2>&1; then
    sh -c "$(curl -fsLS get.chezmoi.io)" -- -b ~/.local/bin
fi
if [ ! -d ~/dotfiles ]; then
    git clone https://github.com/ak110/dotfiles.git ~/dotfiles
fi

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

backup_existing_dotfiles ~/dotfiles
chezmoi init --verbose --source ~/dotfiles --apply

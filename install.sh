#!/bin/bash
set -eux
sh -c "$(curl -fsLS get.chezmoi.io)" -- -b ~/.local/bin
export PATH="$HOME/.local/bin:$PATH"
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

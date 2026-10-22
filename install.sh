#!/bin/bash
set -eu

mode_args=()
while [ "$#" -gt 0 ]; do
    case "$1" in
    --mode)
        [ "$#" -ge 2 ] || {
            echo '--modeにはfullまたはminimalを指定してください。' >&2
            exit 2
        }
        case "$2" in full | minimal) mode_args=(--mode "$2") ;; *)
            echo 'モードはfullまたはminimalを指定してください。' >&2
            exit 2
            ;;
        esac
        shift 2
        ;;
    *)
        echo "不明な引数です: $1" >&2
        exit 2
        ;;
    esac
done

# 前提条件チェック（README の「前提条件(要インストール)」セクション参照）
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

install_chezmoi() {
    local attempt=1
    local installer
    until installer=$(curl -fsSL --connect-timeout 10 --max-time 30 get.chezmoi.io) &&
        sh -c "$installer" -- -b ~/.local/bin; do
        if [ "$attempt" -ge 3 ]; then
            return 1
        fi
        attempt=$((attempt + 1))
        sleep 2
    done
}

# 既に chezmoi が入っていればダウンロードをスキップする（冪等性とテスト用途）
export PATH="$HOME/.local/bin:$PATH"
if ! command -v chezmoi >/dev/null 2>&1; then
    install_chezmoi
fi
if [ ! -d ~/dotfiles ]; then
    git clone https://github.com/ak110/dotfiles.git ~/dotfiles
fi

# 初回のテンプレート評価より前に、依存不要の共通設定入口で保存する。
AGENT_TOOLKIT_CONFIG_DOTFILES_MODE=$(uv run --no-project --no-config --python 3.15 python "$HOME/dotfiles/agent-toolkit/agent_toolkit/_common/dotfiles_mode.py" "${mode_args[@]}")
export AGENT_TOOLKIT_CONFIG_DOTFILES_MODE

# chezmoi 管理対象の既存ファイルをバックアップする
backup_existing_dotfiles() {
    local source_dir="$1"
    local backup_dir
    backup_dir="$HOME/.dotfiles-backup/$(date +%Y%m%d-%H%M%S)"
    local count=0

    while IFS= read -r target; do
        local src="$HOME/$target"
        # ファイルとシンボリックリンクのみ対象とする（ディレクトリはスキップ）
        [ -f "$src" ] || [ -L "$src" ] || continue
        local dest_dir
        dest_dir="$backup_dir/$(dirname "$target")"
        mkdir -p "$dest_dir"
        cp -a "$src" "$dest_dir/"
        count=$((count + 1))
    done < <(chezmoi managed --source="$source_dir")

    if [ "$count" -gt 0 ]; then
        echo "Backed up $count existing files to: $backup_dir"
    fi
}

backup_existing_dotfiles ~/dotfiles
chezmoi init --verbose --source=~/dotfiles --apply

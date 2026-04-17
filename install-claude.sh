#!/bin/bash
# install-claude.sh - ~/.claude/rules/ に Claude Code 用の共通ルールファイルを配置する。
#
# 会社マシンなど dotfiles 全体を入れられない環境向け。GitHub から最新のルールファイルを
# ダウンロードし、既存ファイルの YAML frontmatter (paths 等のカスタマイズ) は維持する。
#
# 使い方:
#   curl -fsSL https://raw.githubusercontent.com/ak110/dotfiles/master/install-claude.sh | bash
#
# テスト時は DOTFILES_RULES_URL 環境変数でベース URL を差し替え可能。

set -euo pipefail

BASE_URL="${DOTFILES_RULES_URL:-https://raw.githubusercontent.com/ak110/dotfiles/master/.chezmoi-source/dot_claude/rules/agent-basics}"
TARGET_DIR="$HOME/.claude/rules/agent-basics"

# 配布対象ファイル一覧 (pytools/claudize.py の _UNCONDITIONAL_RULES / _CONDITIONAL_RULES と一致させること)
FILES=(
    agent.md
    styles.md
)

BACKUP_DIR=""

# 先頭 `---` で始まり、以降に閉じ `---` 行があれば frontmatter 付きと判定する
# (pytools/claudize.py の _split_frontmatter と等価)
# awk は exit で抜けても END を通るため、rc 変数経由で結果を返す
_has_fm() {
    awk '
        BEGIN { rc = 1 }
        NR == 1 { if ($0 !~ /^---/) exit 1; next }
        $0 == "---" { rc = 0; exit }
        END { exit rc }
    ' "$1"
}

# frontmatter (開始 `---` 行から次の `---` 行まで) を出力
_extract_fm() {
    awk '
        NR == 1 { print; next }
        { print }
        NR > 1 && $0 == "---" { exit }
    ' "$1"
}

# body (frontmatter の次の行以降) を出力。frontmatter が無い場合はファイル全体を出力
_extract_body() {
    if _has_fm "$1"; then
        awk '
            found { print; next }
            NR > 1 && $0 == "---" { found = 1; next }
        ' "$1"
    else
        cat "$1"
    fi
}

_process_file() {
    local name="$1"
    local dst="$TARGET_DIR/$name"
    local dl new
    dl=$(mktemp)
    new=$(mktemp)

    curl -fsSL "$BASE_URL/$name" -o "$dl"

    # 既存ファイルに frontmatter があれば維持して body のみ差し替え
    if [ -f "$dst" ] && _has_fm "$dst"; then
        _extract_fm "$dst" >"$new"
        _extract_body "$dl" >>"$new"
    else
        cp "$dl" "$new"
    fi
    rm -f "$dl"

    if [ -f "$dst" ]; then
        if cmp -s "$dst" "$new"; then
            echo "変更なし: $dst"
            rm -f "$new"
            return
        fi
        if [ -z "$BACKUP_DIR" ]; then
            # バックアップは ~/.claude/rules/ の外に置く
            # (rules/ 配下は Claude Code が再帰的に読み込むため、退避先も読まれてしまう)
            BACKUP_DIR="$HOME/.claude/rules-backup/agent-basics-$(date +%Y%m%d-%H%M%S)"
            mkdir -p "$BACKUP_DIR"
        fi
        cp -a "$dst" "$BACKUP_DIR/$name"
        mv "$new" "$dst"
        echo "上書き: $dst"
    else
        mv "$new" "$dst"
        echo "追加: $dst"
    fi
}

# 配布対象外になった旧ファイル一覧（新ファイル配布後に削除する）
# agent-toolkit プラグインの各スキル (coding-standards / plan-mode / bugfix / claude-meta-rules) に
# 移行されたもの。旧レイアウト時代の rules.md / skills.md もそのまま残す。
OBSOLETE_FILES=(
    markdown.md
    rules.md
    skills.md
    python.md
    python-test.md
    typescript.md
    typescript-test.md
    rust.md
    rust-test.md
    csharp.md
    csharp-test.md
    powershell.md
    windows-batch.md
    claude.md
    claude-hooks.md
    claude-rules.md
    claude-skills.md
)

# agent-toolkit プラグインを user scope でインストールする。
# 旧ルールファイルに含まれていた言語別規約・計画モード手順・バグ対応手順・
# Claude設定記述ガイドはすべて agent-toolkit プラグインのスキルへ移行されているため、
# ルール配布と合わせてプラグインの導入を促す。
_install_agent_toolkit() {
    if ! command -v claude >/dev/null 2>&1; then
        echo ""
        echo "agent-toolkit プラグインは未導入です (claude CLI 未検出)。"
        echo "主要ルールは agent-toolkit プラグインのスキルへ移行済みのため、"
        echo "claude CLI 導入後に次のコマンドでインストールすることを推奨します:"
        echo "  claude plugin marketplace add ak110/dotfiles"
        echo "  claude plugin install agent-toolkit@ak110-dotfiles --scope user"
        return
    fi
    echo ""
    echo "agent-toolkit プラグインを user scope にインストールします..."
    claude plugin marketplace add ak110/dotfiles --scope user >/dev/null 2>&1 || true
    claude plugin install agent-toolkit@ak110-dotfiles --scope user >/dev/null 2>&1 || true
    echo "agent-toolkit プラグインの導入を試行しました (既に導入済みならスキップされます)。"
}

main() {
    mkdir -p "$TARGET_DIR"
    for name in "${FILES[@]}"; do
        _process_file "$name"
    done
    for name in "${OBSOLETE_FILES[@]}"; do
        local old="$TARGET_DIR/$name"
        if [ -f "$old" ]; then
            rm -f "$old"
            echo "削除（リネーム済み）: $old"
        fi
    done
    if [ -n "$BACKUP_DIR" ]; then
        echo "バックアップ先: $BACKUP_DIR"
    fi
    _install_agent_toolkit
}

main "$@"

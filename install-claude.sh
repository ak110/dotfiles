#!/bin/bash
# install-claude.sh - ~/.claude/rules/agent-toolkit/ に agent-toolkit ルールファイルを配置する。
#
# 会社マシンなど dotfiles 全体を入れられない環境向け。GitHub から最新のルールファイルを
# 一時ステージングディレクトリへダウンロードし、原子的リネームで配布先を差し替える。
#
# 使い方: Claude Code をインストールしたあとで以下を実行する。
#   curl -fsSL https://raw.githubusercontent.com/ak110/dotfiles/master/install-claude.sh | bash
#
# テスト時は DOTFILES_RULES_URL 環境変数でベース URL を差し替え可能。

set -euo pipefail

BASE_URL="${DOTFILES_RULES_URL:-https://raw.githubusercontent.com/ak110/dotfiles/master/.chezmoi-source/dot_claude/rules/agent-toolkit}"
TARGET_DIR="$HOME/.claude/rules/agent-toolkit"
LEGACY_DIR="$HOME/.claude/rules/agent-basics"
# ステージング先は rules/ の外に置く。
# rules/ 配下に作ると Claude Code が再帰的に読み込んでしまい、差し替え中に二重ロードされる危険がある。
STAGE_ROOT="$HOME/.claude/rules-stage"

# 配布対象ファイル一覧 (install-claude.ps1 の $files と一致させること)
FILES=(
    agent.md
    styles.md
)

STAGE_DIR=""
OLD_DIR=""
# 差し替え完了後は OLD_DIR を削除扱いとするため本フラグで区別する (エラー時の復元を抑止)
REPLACED=0

_cleanup() {
    # エラー終了時に既存の TARGET_DIR を可能な限り復元する。
    if [ "$REPLACED" -eq 0 ] && [ -n "$OLD_DIR" ] && [ -d "$OLD_DIR" ] && [ ! -d "$TARGET_DIR" ]; then
        mv "$OLD_DIR" "$TARGET_DIR" 2>/dev/null || true
    fi
    # STAGE_DIR と OLD_DIR の残骸を削除する。
    [ -n "$STAGE_DIR" ] && [ -d "$STAGE_DIR" ] && rm -rf "$STAGE_DIR"
    [ -n "$OLD_DIR" ] && [ -d "$OLD_DIR" ] && rm -rf "$OLD_DIR"
    return 0
}
trap _cleanup EXIT

_download() {
    local name="$1"
    curl -fsSL "$BASE_URL/$name" -o "$STAGE_DIR/$name"
}

# agent-toolkit プラグインを user scope でインストール・更新する。
# 併せて旧 edit-guardrails プラグインを除去する (agent-toolkit へ改名・統合されたため)。
_install_agent_toolkit() {
    echo ""
    echo "agent-toolkit プラグインを user scope にインストール・更新します..."
    claude plugin marketplace add ak110/dotfiles --scope user >/dev/null 2>&1 || true
    claude plugin marketplace update ak110-dotfiles >/dev/null 2>&1 || true
    claude plugin uninstall edit-guardrails@ak110-dotfiles >/dev/null 2>&1 || true
    claude plugin install agent-toolkit@ak110-dotfiles --scope user >/dev/null 2>&1 || true
    claude plugin update agent-toolkit@ak110-dotfiles --scope user >/dev/null 2>&1 || true
    echo "agent-toolkit プラグインの導入・更新を試行しました (旧 edit-guardrails は削除を試行しました)。"
}

main() {
    if ! command -v claude >/dev/null 2>&1; then
        echo "Claude Code (claude CLI) が見つかりません。" >&2
        echo "Claude Code を先にインストールしてから本スクリプトを再実行してください。" >&2
        exit 1
    fi

    mkdir -p "$(dirname "$TARGET_DIR")" "$STAGE_ROOT"
    STAGE_DIR=$(mktemp -d "$STAGE_ROOT/agent-toolkit.stage.XXXXXX")

    for name in "${FILES[@]}"; do
        _download "$name"
    done

    # 既存の TARGET_DIR を退避し、ステージングを差し替える。
    # mv 同士は同一ファイルシステム上で原子的リネームとして動作する。
    if [ -d "$TARGET_DIR" ]; then
        OLD_DIR="$STAGE_ROOT/agent-toolkit.old.$$"
        mv "$TARGET_DIR" "$OLD_DIR"
    fi
    mv "$STAGE_DIR" "$TARGET_DIR"
    STAGE_DIR=""
    REPLACED=1
    echo "配置: $TARGET_DIR"

    if [ -n "$OLD_DIR" ] && [ -d "$OLD_DIR" ]; then
        rm -rf "$OLD_DIR"
        OLD_DIR=""
    fi

    if [ -d "$LEGACY_DIR" ]; then
        rm -rf "$LEGACY_DIR"
        echo "削除（旧ディレクトリ）: $LEGACY_DIR"
    fi

    _install_agent_toolkit
}

main "$@"

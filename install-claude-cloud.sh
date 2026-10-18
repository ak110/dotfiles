#!/bin/bash
# install-claude-cloud.sh - Claude Codeクラウドの環境セットアップでagent-toolkitを用意する。
#
# クラウド環境の「Setup script」欄へ次の1行を置く。
#   curl -fsSL https://raw.githubusercontent.com/ak110/dotfiles/master/install-claude-cloud.sh | bash
# 環境の「Environment variables」欄には、実行後に表示される2行を保存する。
#
# 次を順に行う。
# - 配布物のリポジトリを取得先へ浅くcloneする（取得済みなら最新へ更新する）
# - agents_serverの依存を依存領域へ事前に準備する（`--check-dependencies`）
# - 環境変数の欄へ保存する値を表示する
#
# install-claude.shはローカルのClaude Code・Codex・uvを必須とし、ルール・Codex plugin・atkラッパーを設定する。
# Codexを持たないクラウドのセットアップでは成立しないため、クラウド用の工程をこのスクリプトへ分けている。
#
# 取得先は AGENT_TOOLKIT_CLOUD_SRC（省略時 /opt/agent-toolkit-src）、
# 依存領域は AGENT_TOOLKIT_RUNTIME_DATA（省略時 /opt/agent-toolkit-data）で変更できる。
# パイプで実行する場合、変数は右側の bash の直前へ置く（例: curl ... | AGENT_TOOLKIT_CLOUD_SRC=/srv/src bash）。
# テスト時は DOTFILES_REPO_URL と DOTFILES_BRANCH で取得元を差し替えられる。

set -euo pipefail

REPO_URL="${DOTFILES_REPO_URL:-https://github.com/ak110/dotfiles.git}"
BRANCH="${DOTFILES_BRANCH:-master}"
SRC_DIR="${AGENT_TOOLKIT_CLOUD_SRC:-/opt/agent-toolkit-src}"
RUNTIME_DATA="${AGENT_TOOLKIT_RUNTIME_DATA:-/opt/agent-toolkit-data}"

_fail() {
    echo "install-claude-cloud: $1" >&2
    exit 1
}

_require_command() {
    command -v "$1" >/dev/null 2>&1 || _fail "$1が見つかりません。$2"
}

main() {
    _require_command git "クラウド環境のイメージにgitがあることを確認してください。"
    _require_command uv "クラウド環境のイメージにuvがあることを確認し、無ければセットアップでuvを導入してから再実行してください。"

    if [ -d "$SRC_DIR/.git" ]; then
        echo "更新: $SRC_DIR"
        git -C "$SRC_DIR" fetch --quiet --depth 1 origin "$BRANCH" ||
            _fail "$REPO_URL のbranch $BRANCH を取得できませんでした。ネットワーク設定（GitHubへの接続）を確認してください。"
        git -C "$SRC_DIR" reset --quiet --hard FETCH_HEAD ||
            _fail "$SRC_DIR を取得した版へ更新できませんでした。$SRC_DIR を削除してから再実行してください。"
    else
        echo "取得: $REPO_URL ($BRANCH) -> $SRC_DIR"
        git clone --quiet --depth 1 --branch "$BRANCH" "$REPO_URL" "$SRC_DIR" ||
            _fail "$REPO_URL のbranch $BRANCH をcloneできませんでした。ネットワーク設定（GitHubへの接続）と取得先の書込権限を確認してください。"
    fi

    echo "依存の準備: $RUNTIME_DATA"
    AGENT_TOOLKIT_RUNTIME_DATA="$RUNTIME_DATA" uv run --no-config --no-project --python 3.15 python \
        "$SRC_DIR/agent-toolkit/scripts/launch.py" agent_toolkit/agents_server_mcp.py --check-dependencies ||
        _fail "agents_serverの依存を準備できませんでした。Python 3.15とPythonパッケージ配布先へのネットワーク接続を確認してください。"

    echo "準備が完了しました。環境の「Environment variables」欄へ次の2行を保存してください。"
    echo "CLAUDE_CODE_PLUGIN_DIRS=$SRC_DIR/agent-toolkit"
    echo "AGENT_TOOLKIT_RUNTIME_DATA=$RUNTIME_DATA"
}

main "$@"

#!/bin/bash
# install-claude.sh - dotfiles 全体を導入できない環境へ agent-toolkit を導入・更新する。
#
# 会社マシンなど dotfiles 全体を導入できない環境向け。次を順に行う。
# - 旧版が配置した常時規範（~/.claude/rules/agent-toolkit/ など）を削除する
# - Claude Code と Codex へ agent-toolkit プラグインを導入・更新する
# - agents_server の依存をウォームアップする
# - 旧 Codex MCP 定義（User scope）を移行する
# - ~/.local/bin/atk へラッパーを配置する
#
# 使い方: Claude Code、Codex、uv をインストールしたあとで以下を実行する。
#   curl -fsSL https://raw.githubusercontent.com/ak110/dotfiles/master/install-claude.sh | bash
#
# テスト時は DOTFILES_RAW_BASE_URL 環境変数でリポジトリのファイルの取得元を差し替え可能。

set -euo pipefail

RAW_BASE_URL="${DOTFILES_RAW_BASE_URL:-https://raw.githubusercontent.com/ak110/dotfiles/master}"
# 旧版が配置した常時規範の配置先。規範は入口のスキル`atk-mode`と手順書へ移したため、残すと廃止した
# スキルの起動を求める旧い規範が読まれ続ける。いずれもインストーラーだけが書き込んでいた。
RETIRED_PATHS=(
    "$HOME/.claude/rules/agent-toolkit"
    "$HOME/.claude/rules/agent-basics"
    "$HOME/.claude/rules-stage"
)
CODEX_PLUGIN_ID="agent-toolkit@ak110-dotfiles"
BASICS_PLUGIN_ID="agent-basics@ak110-dotfiles"
INSTALL_TOOLKIT=1
if [ "${1:-}" = "--basics-only" ]; then INSTALL_TOOLKIT=0; fi
CODEX_JSON_PYTHON=(uv run --no-cache --no-config --no-project --python 3.15 python)
CLAUDE_INSTALLED_PLUGINS="$HOME/.claude/plugins/installed_plugins.json"
CODEX_HOME_DIR="${CODEX_HOME:-$HOME/.codex}"
CODEX_PLUGIN_CACHE_ROOT="$CODEX_HOME_DIR/plugins/cache/ak110-dotfiles/agent-toolkit"

CODEX_PLUGIN_UPDATED=0
CODEX_SAVED_CACHE_RETRY="次回の実行が退避先から戻します。解消しない場合は退避先を変更せず、表示された内容とパスをdotfilesの作者へ報告してください。"

_print_codex_restart_notice() {
    echo "Codex pluginを更新しました。実行中のCodexセッションを終了してから、次のコマンドでapp-server daemonを再起動してください。" >&2
    echo "codex app-server daemon restart" >&2
}

_codex_daemon_running() {
    codex app-server daemon version >/dev/null 2>&1
}

_on_exit() {
    local status=$?
    trap - EXIT
    if [ "$CODEX_PLUGIN_UPDATED" -eq 1 ] && _codex_daemon_running; then
        _print_codex_restart_notice
    fi
    exit "$status"
}
trap _on_exit EXIT

_require_command() {
    local command_name="$1"
    local product_name="$2"
    local install_url="$3"
    if ! command -v "$command_name" >/dev/null 2>&1; then
        echo "$product_name ($command_name CLI) が見つかりません。" >&2
        echo "$install_url を参照して先にインストールしてください。" >&2
        return 1
    fi
}

# 失敗すると導入を続けられない Claude CLI の更新を実行する。
# 成功時は標準出力を抑える。CLI が失敗診断を標準出力へ書く場合があるため、失敗時は捕捉した標準出力も表示する。
# 標準エラーは捕捉せずそのまま出力し、成功時の警告表示を従来どおり保つ。
_run_required_claude() {
    local output=""
    local status=0
    output=$(claude "$@") || status=$?
    if [ "$status" -eq 0 ]; then
        return 0
    fi
    if [ -n "$output" ]; then
        printf '%s\n' "$output" >&2
    fi
    echo "失敗したコマンド: claude $*（終了コード: $status）" >&2
    echo "次の操作: 上の診断から原因を解消し、失敗したコマンドを再実行して成功を確認してから、install-claude.shを再実行してください。解消できない場合は、表示された診断と失敗したコマンドをdotfilesの作者へ報告してください。" >&2
    return "$status"
}

# agent-toolkit プラグインを user scope でインストール・更新する。
# 併せて旧 edit-guardrails プラグインを除去する（現在は agent-toolkit に統合されている）。
_install_agent_toolkit() {
    echo ""
    echo "agent-toolkit プラグインを user scope にインストール・更新します..."
    claude plugin marketplace add ak110/dotfiles --scope=user >/dev/null 2>&1 || true
    _run_required_claude plugin marketplace update ak110-dotfiles
    claude plugin uninstall edit-guardrails@ak110-dotfiles >/dev/null 2>&1 || true
    claude plugin install agent-toolkit@ak110-dotfiles --scope=user >/dev/null 2>&1 || true
    _run_required_claude plugin update agent-toolkit@ak110-dotfiles --scope=user
    echo "Claude Code側のagent-toolkitプラグインを設定しました。"
}

_install_agent_basics() {
    echo "agent-basics プラグインを user scope にインストール・更新します..."
    claude plugin marketplace add ak110/dotfiles --scope=user >/dev/null 2>&1 || true
    _run_required_claude plugin marketplace update ak110-dotfiles
    claude plugin install "$BASICS_PLUGIN_ID" --scope=user >/dev/null 2>&1 || true
    _run_required_claude plugin update "$BASICS_PLUGIN_ID" --scope=user
}

_codex_plugin_state() {
    local plugin_json
    if ! plugin_json=$(codex plugin list --json --marketplace ak110-dotfiles); then
        return 1
    fi
    "${CODEX_JSON_PYTHON[@]}" - "${1:-$CODEX_PLUGIN_ID}" "$plugin_json" <<'PY'
import json
import sys

plugin_id = sys.argv[1]
try:
    data = json.loads(sys.argv[2])
    installed = data["installed"]
except (json.JSONDecodeError, KeyError, TypeError):
    raise SystemExit(1)
if not isinstance(installed, list):
    raise SystemExit(1)
plugin = None
for item in installed:
    if not isinstance(item, dict) or not isinstance(item.get("pluginId"), str):
        raise SystemExit(1)
    if item["pluginId"] != plugin_id:
        continue
    version = item.get("version")
    enabled = item.get("enabled")
    if not isinstance(version, str) or not isinstance(enabled, bool):
        raise SystemExit(1)
    if plugin is None:
        plugin = {"enabled": enabled, "present": True, "version": version}
if plugin is None:
    print('{"present": false}')
else:
    print(json.dumps(plugin, sort_keys=True))
PY
}

_codex_state_value() {
    local state="$1"
    local key="$2"
    "${CODEX_JSON_PYTHON[@]}" - "$key" "$state" <<'PY'
import json
import sys

value = json.loads(sys.argv[2]).get(sys.argv[1])
if isinstance(value, bool):
    print(str(value).lower())
elif value is not None:
    print(value)
PY
}

_codex_expected_plugin_version() {
    local marketplace_json
    if ! marketplace_json=$(codex plugin marketplace list --json); then
        return 1
    fi
    "${CODEX_JSON_PYTHON[@]}" - "$marketplace_json" "${1:-agent-toolkit}" <<'PY'
import json
import pathlib
import sys

try:
    data = json.loads(sys.argv[1])
    marketplaces = data["marketplaces"]
except (json.JSONDecodeError, KeyError, TypeError):
    raise SystemExit(1)
for item in marketplaces:
    if isinstance(item, dict) and item.get("name") == "ak110-dotfiles" and isinstance(item.get("root"), str):
        try:
            root = pathlib.Path(item["root"]).resolve()
            marketplace = json.loads((root / ".agents/plugins/marketplace.json").read_text(encoding="utf-8"))
            entry = next(plugin for plugin in marketplace["plugins"] if plugin.get("name") == sys.argv[2])
            source = entry["source"]
            if source.get("source") != "local" or not isinstance(source.get("path"), str):
                raise ValueError
            plugin_root = (root / source["path"]).resolve()
            plugin_root.relative_to(root)
            manifest = plugin_root / ".codex-plugin/plugin.json"
            data = json.loads(manifest.read_text(encoding="utf-8"))
            if data["name"] != sys.argv[2]:
                raise ValueError
            version = data["version"]
        except (OSError, UnicodeError, json.JSONDecodeError, KeyError, StopIteration, TypeError, ValueError):
            raise SystemExit(1)
        if not isinstance(version, str):
            raise SystemExit(1)
        print(version)
        raise SystemExit(0)
raise SystemExit(1)
PY
}

# 旧versionはCODEX_HOME直下の固定の退避先へ移動する。別ボリュームではmvが複製する。
# 中断した実行が残した退避は、次回の実行が_restore_codex_plugin_cachesで戻す。
# 目標versionはCLIが置き換えるため元の場所に残して複製し、plugin addの失敗時に戻せるようにする。
# 移動できない旧versionも元の場所に残して複製し、CLIが途中まで消した場合に復元で補えるよう~copiedの印を付ける。
_snapshot_codex_plugin_caches() {
    local destination="$1"
    local target_version="$2"
    local cache=""
    [ -d "$CODEX_PLUGIN_CACHE_ROOT" ] || return 0
    mkdir -p "$destination" || return 1
    for cache in "$CODEX_PLUGIN_CACHE_ROOT"/*; do
        [ -d "$cache" ] && [ ! -L "$cache" ] || continue
        # 復元途中の複製（~restoring）など、版でない名前は退避しない。
        case "${cache##*/}" in *'~'*) continue ;; esac
        if [ "${cache##*/}" != "$target_version" ]; then
            mv "$cache" "$destination/" 2>/dev/null || true
            if [ -e "$cache" ]; then
                : >"$destination/${cache##*/}~copied" || return 1
            fi
        fi
        if [ -e "$cache" ]; then
            cp -a "$cache" "$destination/" || return 1
        fi
    done
}

# 退避した版にあって元のパスに無いファイルとディレクトリを補う。
# 元のパスに残るファイルはCLIが消せなかった使用中のファイルであり、置き換えずに残す。
# 各ファイルは隣で複製を仕上げてから名前の変更で置き、途中で止まっても欠けたファイルを元の名前で残さない。
_complete_codex_plugin_cache() {
    local saved="$1"
    local destination="$2"
    (cd "$saved" && find . -mindepth 1 -type d) | while IFS= read -r item; do
        mkdir -p "$destination/$item" || exit 1
    done || return 1
    (cd "$saved" && find . ! -type d) | while IFS= read -r item; do
        if [ -e "$destination/$item" ] || [ -L "$destination/$item" ]; then
            continue
        fi
        rm -f "$destination/$item~restoring" || exit 1
        cp -a "$saved/$item" "$destination/$item~restoring" || exit 1
        mv "$destination/$item~restoring" "$destination/$item" || exit 1
    done
}

_path_device() {
    stat -L -c %d -- "$1" 2>/dev/null || stat -L -f %d -- "$1"
}

_restore_codex_plugin_caches() {
    local saved_root="$1"
    local installed_version="$2"
    local replace_existing="$3"
    local saved=""
    local version=""
    local destination=""
    local replace_marker=""
    local copied_marker=""
    local staging=""
    local same_volume=0
    [ -d "$saved_root" ] || return 0
    mkdir -p "$CODEX_PLUGIN_CACHE_ROOT" || return 1
    if [ "$(_path_device "$saved_root")" = "$(_path_device "$CODEX_PLUGIN_CACHE_ROOT")" ]; then
        same_volume=1
    fi
    for saved in "$saved_root"/*; do
        [ -d "$saved" ] || continue
        version=${saved##*/}
        destination="$CODEX_PLUGIN_CACHE_ROOT/$version"
        # 置き換えの途中で失敗した版は印を残し、次回の実行も途中まで消えた元のパスより退避した内容を優先する。
        replace_marker="$saved_root/$version~replace"
        # 複製で退避した使用中の版は使用中のファイルが残って置き換えられないため、元のパスに無いファイルだけを補う。
        copied_marker="$saved_root/$version~copied"
        staging="$destination~restoring"
        rm -rf "$staging" || return 1
        if { [ "$replace_existing" -eq 1 ] || [ -e "$replace_marker" ]; } && [ ! -e "$copied_marker" ] && { [ -e "$destination" ] || [ -L "$destination" ]; }; then
            : >"$replace_marker" || return 1
            rm -rf "$destination" || return 1
        fi
        if [ "$version" = "$installed_version" ] || [ -e "$destination" ] || [ -L "$destination" ]; then
            if [ -e "$copied_marker" ] && [ "$version" != "$installed_version" ]; then
                _complete_codex_plugin_cache "$saved" "$destination" || return 1
            fi
            rm -f "$replace_marker" "$copied_marker" || return 1
            rm -rf "$saved" || return 1
            continue
        fi
        if [ "$same_volume" -eq 1 ]; then
            mv "$saved" "$destination" || return 1
            rm -f "$replace_marker" "$copied_marker" || return 1
        else
            # 別ボリュームのmvは複製と削除を兼ね、途中の失敗で元のパスに欠けた版を残し得るため、
            # 複製を仕上げてから元のパスへ置き、その後に退避を削除する。
            cp -a "$saved" "$staging" || return 1
            mv "$staging" "$destination" || return 1
            rm -f "$replace_marker" "$copied_marker" || return 1
            rm -rf "$saved" || return 1
        fi
    done
    # 版を元のパスへ戻した直後に中断すると、対応する退避の無い印だけが残る。
    rm -f "$saved_root"/*~replace "$saved_root"/*~copied || return 1
    rmdir "$saved_root" || return 1
    rmdir "${saved_root%/*}" "${saved_root%/*/*}" 2>/dev/null || true
}

# 期待する版はGitHubのmarketplace（ak110/dotfiles）を登録したCodexが取得したmanifestから読む。
# dotfilesのpost-applyはローカルの作業ツリーをmarketplaceとして登録し、その版を期待値にする。
# 単体インストーラーは作業ツリーを前提にできないため、配布元の違いをそのまま残す。
_install_codex_plugin() {
    local plugin_name="${1:-agent-toolkit}"
    local CODEX_PLUGIN_ID="$plugin_name@ak110-dotfiles"
    local CODEX_PLUGIN_CACHE_ROOT="$CODEX_HOME_DIR/plugins/cache/ak110-dotfiles/$plugin_name"
    local before_state=""
    local before_state_known=0
    local expected_version=""
    local after_state=""
    local after_present="false"
    local after_version=""
    local after_enabled="false"
    local hook_wrapper="$HOME/.local/bin/atk-hook"
    local hook_stage=""
    local saved_root="$CODEX_HOME_DIR/.dotfiles-plugin-cache-saved/ak110-dotfiles/$plugin_name"
    local post_add_error=""
    local skill=""
    local skills_present=0
    echo "Codex側の$plugin_name プラグインを設定します..."
    if ! _restore_codex_plugin_caches "$saved_root" "" 0; then
        echo "前回の実行が退避した旧cacheの復旧に失敗しました。退避先を保持します: $saved_root" >&2
        echo "$CODEX_SAVED_CACHE_RETRY" >&2
        return 1
    fi
    codex plugin marketplace add ak110/dotfiles --json >/dev/null 2>&1 || true
    codex plugin marketplace upgrade ak110-dotfiles --json >/dev/null
    expected_version=$(_codex_expected_plugin_version "$plugin_name")
    if ! before_state=$(_codex_plugin_state); then
        echo "Codex plugin更新前の状態を確認できません。" >&2
        return 1
    fi
    before_state_known=1
    if [ "$plugin_name" = "agent-toolkit" ]; then
        mkdir -p "$HOME/.local/bin"
        hook_stage=$(mktemp "$HOME/.local/bin/atk-hook.XXXXXX")
        if ! curl -fsSL "$RAW_BASE_URL/agent-toolkit/scripts/atk-hook" -o "$hook_stage"; then
            rm -f "$hook_stage"
            return 1
        fi
        chmod 755 "$hook_stage"
        mv "$hook_stage" "$hook_wrapper"
    fi
    if ! _snapshot_codex_plugin_caches "$saved_root" "$expected_version"; then
        echo "Codex plugin更新前の旧cacheを退避できませんでした。" >&2
        _restore_codex_plugin_caches "$saved_root" "" 0 || echo "$CODEX_SAVED_CACHE_RETRY 退避先: $saved_root" >&2
        return 1
    fi
    if ! codex plugin add "$CODEX_PLUGIN_ID" --json >/dev/null; then
        if ! _restore_codex_plugin_caches "$saved_root" "" 1; then
            echo "Codex plugin addと旧cacheの復旧に失敗しました。退避先を保持します: $saved_root" >&2
            echo "$CODEX_SAVED_CACHE_RETRY" >&2
        fi
        return 1
    fi
    if ! after_state=$(_codex_plugin_state); then
        post_add_error="Codex plugin更新後の状態を確認できません。"
    else
        after_present=$(_codex_state_value "$after_state" present)
        after_version=$(_codex_state_value "$after_state" version)
        after_enabled=$(_codex_state_value "$after_state" enabled)
        if [ "$plugin_name" = "agent-toolkit" ] && [ "$before_state_known" -eq 1 ] && [ "$before_state" != "$after_state" ]; then
            CODEX_PLUGIN_UPDATED=1
        fi
        if [ "$after_present" != "true" ] || [ "$after_enabled" != "true" ] || [ "$after_version" != "$expected_version" ]; then
            post_add_error="Codex plugin更新後の状態が期待値と一致しません。"
        elif [ "$plugin_name" = "agent-toolkit" ] && [ ! -f "$CODEX_PLUGIN_CACHE_ROOT/$expected_version/agent_toolkit/hook.py" ]; then
            post_add_error="Codex plugin更新後のhook実体を確認できません。"
        elif [ "$plugin_name" = "agent-basics" ]; then
            for skill in "$CODEX_PLUGIN_CACHE_ROOT/$expected_version/skills"/*/SKILL.md; do
                if [ -f "$skill" ]; then
                    skills_present=1
                    break
                fi
            done
            if [ "$skills_present" -eq 0 ]; then
                post_add_error="Codex plugin更新後のskills実体を確認できません。"
            fi
        fi
    fi
    # 既存セッションが参照する全旧パスを関数の終了時に成立させるため、復旧は保護区間の終端で行う。
    if ! _restore_codex_plugin_caches "$saved_root" "$expected_version" 0; then
        echo "Codex plugin更新後の旧cache復旧に失敗しました。退避先を保持します: $saved_root" >&2
        echo "$CODEX_SAVED_CACHE_RETRY" >&2
        return 1
    fi
    if [ -n "$post_add_error" ]; then
        echo "$post_add_error" >&2
        return 1
    fi
    echo "Codex側の$plugin_name プラグインを設定しました。"
}

_install_codex_basics() {
    _install_codex_plugin agent-basics
}

_claude_plugin_scripts() {
    [ -f "$CLAUDE_INSTALLED_PLUGINS" ] || return 0
    uv run --no-cache --no-config --no-project --python 3.15 python - "$CLAUDE_INSTALLED_PLUGINS" "$CODEX_PLUGIN_ID" <<'PY'
import json
import pathlib
import sys

installed_plugins = pathlib.Path(sys.argv[1])
plugin_id = sys.argv[2]
try:
    data = json.loads(installed_plugins.read_text(encoding="utf-8"))
    entries = data["plugins"].get(plugin_id, [])
except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError, AttributeError):
    raise SystemExit(1)
if not isinstance(entries, list):
    raise SystemExit(1)
for entry in entries:
    install_path = entry.get("installPath") if isinstance(entry, dict) else None
    if isinstance(install_path, str):
        print(pathlib.Path(install_path) / "agent_toolkit/agents_server_mcp.py")
PY
}

_warm_agents_server_path() {
    local script_path="$1"
    local project_root=""
    if [ ! -f "$script_path" ]; then
        echo "agents_serverのウォームアップ対象が存在しないためスキップします: $script_path" >&2
        return 0
    fi
    project_root="$(dirname "$(dirname "$script_path")")"
    if ! uv run --no-cache --no-config --no-project --python 3.15 python "$project_root/scripts/launch.py" --prepare "$script_path" </dev/null >/dev/null; then
        echo "agents_serverのuv環境ウォームアップに失敗しました: $script_path" >&2
        echo "次の操作: install-claude.shを再実行してください。" >&2
        return 0
    fi
    if ! uv run --no-cache --no-config --no-project --python 3.15 python "$project_root/scripts/launch.py" "$script_path" --check-dependencies </dev/null >/dev/null; then
        echo "agents_serverの依存検査に失敗しました: $script_path" >&2
        echo "次の操作: install-claude.shを再実行してください。" >&2
    fi
    return 0
}

_warm_agents_server() {
    local claude_scripts=""
    local expected_version=""
    local existing=""
    local script_path=""
    local duplicate=0
    local -a script_paths=()
    local -a warmed_paths=()
    if claude_scripts=$(_claude_plugin_scripts); then
        while IFS= read -r script_path; do
            [ -n "$script_path" ] && script_paths+=("$script_path")
        done <<<"$claude_scripts"
    else
        echo "Claude Code plugin一覧からagents_serverのウォームアップ対象を解決できないためClaude Code分をスキップします。" >&2
    fi
    if expected_version=$(_codex_expected_plugin_version); then
        script_paths+=("$CODEX_PLUGIN_CACHE_ROOT/$expected_version/agent_toolkit/agents_server_mcp.py")
    else
        echo "Codex pluginからagents_serverのウォームアップ対象バージョンを解決できないためCodex分をスキップします。" >&2
    fi
    for script_path in "${script_paths[@]}"; do
        duplicate=0
        for existing in "${warmed_paths[@]}"; do
            if [ "$existing" = "$script_path" ]; then
                duplicate=1
                break
            fi
        done
        [ "$duplicate" -eq 0 ] || continue
        warmed_paths+=("$script_path")
        _warm_agents_server_path "$script_path"
    done
}

_legacy_user_codex_mcp_status() {
    local config_path="$HOME/.claude.json"
    uv run --no-cache --no-config --no-project --python 3.15 python - "$config_path" <<'PY'
import json
import pathlib
import sys

config_path = pathlib.Path(sys.argv[1])
if not config_path.exists():
    raise SystemExit(1)
try:
    config = json.loads(config_path.read_text(encoding="utf-8"))
except (OSError, UnicodeError, json.JSONDecodeError):
    raise SystemExit(2)
if not isinstance(config, dict):
    raise SystemExit(2)
if "mcpServers" not in config:
    raise SystemExit(1)
servers = config["mcpServers"]
if not isinstance(servers, dict):
    raise SystemExit(2)
value = servers.get("codex")
if not isinstance(value, dict):
    raise SystemExit(1)
if set(value) - {"type", "command", "args", "timeout", "env"}:
    raise SystemExit(3)
if value.get("type") not in (None, "stdio"):
    raise SystemExit(3)
# argsは文字列1件の配列、timeoutは数値7200000だけを旧定義とみなす。
# 判定ケースは`pytools/_internal/legacy_codex_mcp_cases.json`にあり、PowerShell版とpytoolsの実装も同じケースでテストする。
if value.get("command") != "codex" or value.get("args") != ["mcp-server"]:
    raise SystemExit(3)
if value.get("timeout") not in (None, 7200000):
    raise SystemExit(3)
# 旧installerが使う`claude mcp add`は`-e`未指定でもenvを空で書き込む。
# 値を持つenvはユーザーが加えた設定として保持する。
env = value.get("env")
if env is not None and env != {}:
    raise SystemExit(3)
raise SystemExit(0)
PY
}

# 判定できない設定では変更せずに失敗で終える（fail-closed）。dotfilesのpost-applyは同じ判定で何もせず続行するが、
# 単体インストーラーは導入手順の全体が1回の実行であり、ユーザーが失敗の内容から設定を直して再実行できるためである。
_migrate_legacy_codex_mcp() {
    local status
    if _legacy_user_codex_mcp_status; then
        status=0
    else
        status=$?
    fi
    if [ "$status" -eq 1 ]; then
        return 0
    fi
    if [ "$status" -eq 3 ]; then
        echo "User scopeのcodex MCP定義はユーザー固有設定のため保持します。必要なら claude mcp remove --scope user codex を手動実行してください。" >&2
        return 0
    fi
    if [ "$status" -ne 0 ]; then
        echo "$HOME/.claude.jsonから旧Codex MCPのUser scope登録を判定できません。設定を変更せず終了します。" >&2
        return 1
    fi
    if claude mcp remove --scope user codex >/dev/null; then
        echo "旧Codex MCPのUser scope登録を削除しました。"
        return 0
    fi
    echo "旧Codex MCPのUser scope登録を削除できませんでした。" >&2
    return 1
}

# ~/.local/bin/atk へラッパーを配置する。
# インストール済み agent-toolkit プラグインの最新バージョンを動的解決するため、
# 参照先パス（cache/<marketplace>/agent-toolkit/<version>/scripts/atk）を実行時に決定する形とする。
# 直接シンボリックリンクを作成するとバージョン更新のたびに追随が必要となるため、実行時解決のラッパーを採用する。
_install_atk_wrapper() {
    local bin_dir="$HOME/.local/bin"
    local wrapper="$bin_dir/atk"
    mkdir -p "$bin_dir"
    cat >"$wrapper" <<'EOF'
#!/bin/bash
# agent-toolkit プラグイン付属の atk コマンドを最新バージョンから起動するラッパー。
# install-claude.sh が生成する。更新したい場合は同スクリプトを再実行する。
# バージョン名の自然順で最新実体を選択する。
set -euo pipefail
plugin_cli_dir=$(
    # プラグイン外のランチャーだけは、導入済み旧版の配置も起動対象に含める。
    for candidate in "$HOME"/.claude/plugins/cache/*/agent-toolkit/*/scripts "$HOME"/.claude/plugins/cache/*/agent-toolkit/*/bin; do
        [ -x "$candidate/atk" ] || continue
        version_dir=${candidate%/*}
        printf '%s\t%s\n' "${version_dir##*/}" "$candidate"
    done | sort -t "$(printf '\t')" -k1,1V | tail -1 | cut -f2-
)
if [ -z "$plugin_cli_dir" ] || [ ! -x "$plugin_cli_dir/atk" ]; then
    echo "atk: agent-toolkit プラグインが見つかりません。install-claude.sh を再実行してください。" >&2
    exit 1
fi
exec "$plugin_cli_dir/atk" "$@"
EOF
    chmod +x "$wrapper"
    echo "配置: $wrapper"
    if ! (echo "$PATH" | tr ':' '\n' | grep -qxF "$bin_dir"); then
        echo "注意: '$bin_dir' が PATH に含まれていません。~/.bashrc 等で PATH に追加してください。" >&2
    fi
}

_remove_retired_rules() {
    local path=""
    for path in "${RETIRED_PATHS[@]}"; do
        if [ -e "$path" ] || [ -L "$path" ]; then
            rm -rf -- "$path"
            echo "削除（旧い常時規範）: $path"
        fi
    done
}

main() {
    _require_command claude "Claude Code" "https://docs.anthropic.com/ja/docs/claude-code/overview"
    _require_command codex "Codex" "https://developers.openai.com/codex/cli/"
    if [ "$INSTALL_TOOLKIT" -eq 0 ]; then
        _require_command python3 "Python 3" "https://www.python.org/downloads/"
        CODEX_JSON_PYTHON=(python3)
        _install_agent_basics
        _install_codex_basics
        echo "Claude Code・Codexのagent-basics設定が完了しました。"
        return 0
    fi
    _require_command uv "uv" "https://docs.astral.sh/uv/getting-started/installation/"

    _remove_retired_rules

    _install_agent_basics
    _install_codex_basics
    if [ "$INSTALL_TOOLKIT" -eq 1 ]; then
        _install_agent_toolkit
        _install_codex_plugin
        _warm_agents_server
        _migrate_legacy_codex_mcp
        _install_atk_wrapper
    fi
    echo "Claude Code・Codex、agent-toolkit、atkの設定が完了しました。"
}

main "$@"

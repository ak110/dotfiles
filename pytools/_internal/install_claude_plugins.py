"""dotfiles同梱のClaude Code pluginを自動インストール・更新する。

`chezmoi apply`後処理（`pytools.post_apply`）から呼ばれる。
対象プラグインは`.claude-plugin/marketplace.json`の`plugins[]`をSSOTとして動的に決定する。
前提条件（`claude` CLI / `uv` CLIがPATHにある）を満たさない場合は完全にスキップし、
dotfiles apply全体の失敗にはしない。前提を満たしたうえで管理対象pluginの導入・更新が失敗した場合と、
導入後の版・有効状態が目標と一致しない場合は例外を送出し、後処理の工程の失敗として扱わせる。
"""

import json
import logging
import shutil
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Literal, cast

from agent_toolkit._common import dotfiles_mode

from pytools._internal import (
    claude_common,
    claude_marketplace,
    claude_plugin_references,
    common,
    deferred_updates,
    log_format,
    post_apply_outcome,
)

logger = logging.getLogger(__name__)


# plugin cache の version 直下に揃っている必要があるファイル。
# いずれかが欠けると `uv run --project <キャッシュ>` がそのディレクトリをプロジェクトとして
# 解決できず、対象 plugin の起動スクリプトが import に失敗する。
_PLUGIN_CACHE_REQUIRED_FILES: tuple[str, ...] = ("pyproject.toml", "uv.lock", ".claude-plugin/plugin.json")

# directory型marketplaceからの導入で、主作業ツリーのgitignore対象ごと版ディレクトリへ複製される開発用の生成物。
# `.venv`は主作業ツリーの開発環境の実バイトの複製（版ごとに約0.9G）で、pluginの実行は`scripts/launch.py`が
# 共有の依存環境を使うため参照しない。`agent-toolkit/scripts/atk-hook`の`_COPY_IGNORE`から、
# 実行時にも生成される`__pycache__`を除いた集合。
_PLUGIN_CACHE_DEVELOPMENT_ARTIFACTS: tuple[str, ...] = (".venv", ".mypy_cache", ".hypothesis", ".pytest_cache", ".ruff_cache")

# インストール済みかつ`enabledPlugins`で`false`にされておらず有効なものを `run()` 中に `claude plugin disable` で無効化する。
_AUTO_DISABLED_PLUGIN_IDS: frozenset[str] = frozenset(
    {
        "serena@claude-plugins-official",
        "superpowers@claude-plugins-official",
        "security-guidance@claude-plugins-official",
        "pr-review-toolkit@claude-plugins-official",
        "code-simplifier@claude-plugins-official",
        "commit-commands@claude-plugins-official",
        "code-review@claude-plugins-official",
        "claude-md-management@claude-plugins-official",
    }
)

# 未インストールなら `claude plugin install`、明示的にfalseなら
# `claude plugin enable` の提案として案内に表示する。
_AUTO_ENABLED_PLUGIN_IDS: frozenset[str] = frozenset(
    {
        "context7@claude-plugins-official",
        "typescript-lsp@claude-plugins-official",
        "skill-creator@claude-plugins-official",
    }
)


def run() -> post_apply_outcome.PostApplyOutcome:
    """Claude Code pluginをインストール・更新し、ユーザーへ案内する推奨コマンドを結果へ含める。

    管理対象pluginの導入・更新の失敗と、導入後の状態が目標と一致しない場合は、警告を出力してスキップと数える。
    外部marketplaceのpluginの自動無効化（設定の書き換え）の失敗は失敗と数える。
    稼働中の参照を守るためにplugin cacheの修復を延期した場合は延期と数える。
    """
    if dotfiles_mode.is_minimal():
        return post_apply_outcome.PostApplyOutcome()
    try:
        changed, recommendations, disable_failed_count, artifact_failed_count, deferred = _install_plugins()
    except RuntimeError as error:
        logger.warning(
            log_format.format_status("plugins", f"導入または更新に失敗: {error}。`update-dotfiles`を再実行する"),
        )
        return post_apply_outcome.PostApplyOutcome()
    failures: list[str] = []
    if disable_failed_count:
        failures.append(f"外部plugin {disable_failed_count} 件の自動無効化に失敗")
    if artifact_failed_count:
        failures.append(f"plugin cache の開発用生成物 {artifact_failed_count} 件の削除に失敗")
    failure = "、".join(failures) or None
    return post_apply_outcome.PostApplyOutcome(
        changed=changed,
        recommendations=tuple(recommendations),
        failure=failure,
        next_action="`update-dotfiles`を再実行して失敗した処理をやり直す。" if failure is not None else None,
        deferred=deferred,
    )


def _install_plugins() -> tuple[bool, list[str], int, int, bool]:
    """導入・更新・自動無効化を行い、結果を返す。

    戻り値は`(変更の有無, 推奨コマンド, 自動無効化の失敗件数, 開発用生成物の削除の失敗件数, 修復か削除の延期の有無)`。

    Raises:
        RuntimeError: 管理対象pluginの導入・更新に失敗した場合、または導入後の状態が目標と一致しない場合。
    """
    if not _prerequisites_ok():
        return False, [], 0, 0, False

    external_changed = _remove_unused_plugins()

    dotfiles_root = common.find_dotfiles_root()
    if dotfiles_root is None:
        logger.info(
            log_format.format_status(
                "plugins",
                "dotfiles ルート (marketplace.json) が見つからずスキップ。`update-dotfiles`を再実行する",
            ),
        )
        return external_changed, [], 0, 0, False

    # 対象プラグインは marketplace.json の plugins[] 全件から動的に決める。
    target_versions, deprecated_names = _read_target_info(dotfiles_root)
    if not target_versions and not deprecated_names:
        logger.info(
            log_format.format_status(
                "plugins",
                "marketplace.json に対象 plugin が無いためスキップ。導入対象が無いため対応不要",
            ),
        )
        return external_changed, [], 0, 0, False

    if not claude_marketplace.ensure_marketplace():
        return external_changed, [], 0, 0, False

    # marketplace の修復は remove/add により導入済み登録を変更し得るため、
    # 修復完了後の実体だけを導入判定へ用いる。
    raw_data: object = _read_installed_plugins_from_file()
    if raw_data is None:
        raw_data = _get_installed_plugins_raw()
        if raw_data is None:
            logger.info(
                log_format.format_status(
                    "plugins",
                    "インストール済み plugin 一覧の取得に失敗したためスキップ。`update-dotfiles`を再実行する",
                ),
            )
            return external_changed, [], 0, 0, False

    any_change = False

    # deprecated プラグインをアンインストール
    for name in deprecated_names:
        if _uninstall_deprecated(name, raw_data):
            any_change = True

    # user scope のバージョン辞書を取得
    installed = _extract_plugin_version_map(raw_data)

    # directory 型登録が健全な環境では、dotfiles 実体からキャッシュへの同期を
    # `plugin install` の再実行で毎回行う。`plugin update` は version 一致時 no-op になるため
    # 採用不可 (chezmoi apply 環境での実測: 2026-04-20)。
    is_directory_type = claude_marketplace.is_directory_type_registered()

    # 旧 GitHub 型が残存する環境では、インストール済みプラグインにバージョン不一致があるときだけ
    # marketplace メタデータを refresh する (CLI 起動コスト節約)。
    # directory 型では refresh は validation のみでキャッシュ同期しないため呼ばない。
    if not is_directory_type and any(
        name in installed and installed[name] != target for name, target in target_versions.items() if target
    ):
        claude_marketplace.refresh_marketplace()

    latest_count = 0
    updated_count = 0
    installed_count = 0
    resynced_count = 0
    # 管理対象の導入・更新に失敗したplugin名と操作。サマリ行の後に工程の失敗として送出する。
    failed_operations: list[str] = []
    # CLI が成功終了した plugin だけを、キャッシュ実体の検査対象にする。
    cache_check_names: list[str] = []
    for name, target in target_versions.items():
        # project scope に残存するエントリを除去 (user scope 移行用)
        _cleanup_old_project_scope(name, raw_data)

        current = installed.get(name)
        if current is None:
            if _install_plugin(name):
                any_change = True
                installed_count += 1
                cache_check_names.append(name)
            else:
                failed_operations.append(f"{name} (install)")
        elif target and current != target:
            logger.info(log_format.format_status(name, f"更新を検出: {current} -> {target}"))
            if _update_plugin(name):
                any_change = True
                updated_count += 1
                cache_check_names.append(name)
            else:
                failed_operations.append(f"{name} (update)")
        elif is_directory_type:
            # directory 型登録が健全かつ version 一致の場合、dotfiles 側の編集を反映するため
            # `plugin install` を再実行してキャッシュを最新化する。
            logger.info(
                log_format.format_status(name, f"最新 ({current or '不明'}) のため directory 型キャッシュを再同期"),
            )
            if _install_plugin(name):
                any_change = True
                resynced_count += 1
                cache_check_names.append(name)
            else:
                failed_operations.append(f"{name} (install)")
        else:
            logger.info(log_format.format_status(name, f"最新 ({current or '不明'})"))
            latest_count += 1

    # CLI が成功終了しても、同じ version を既に導入済みと判定した場合はキャッシュが検証されない。
    # Claude Code が次回起動時に読む実体を確認し、欠落していれば 1 回だけ再インストールで修復する。
    cache_deferred = False
    for name in cache_check_names:
        cache_state = _ensure_plugin_cache_complete(name)
        any_change = any_change or cache_state == "repaired"
        cache_deferred = cache_deferred or cache_state == "deferred"
    artifact_failed_count = 0
    for name in cache_check_names:
        removed, failed, held = _remove_development_artifacts(name)
        any_change = any_change or removed
        artifact_failed_count += failed
        cache_deferred = cache_deferred or held

    # 外部 marketplace の自動 disable 実行と install/enable 推奨コマンド算出 (公式プラグイン)。
    # 対象は ak110-dotfiles 以外の marketplace であり、上記のインストールループで
    # 状態が変わらないため、冒頭で取得した raw_data を再利用してよい。
    enabled_map = _read_enabled_plugins_from_file()
    disabled_count, disable_failed_count = _auto_disable_plugins(raw_data, enabled_map)
    if disabled_count:
        any_change = True
    recommendations = compute_recommended_commands(raw_data, enabled_map)

    failed_count = len(failed_operations) + disable_failed_count
    logger.info(
        log_format.format_status(
            "plugins",
            f"サマリ: 最新 {latest_count} 件 / 更新 {updated_count} 件 / 新規 {installed_count} 件"
            + (f" / 再同期 {resynced_count} 件" if resynced_count else "")
            + (f" / 自動無効化 {disabled_count} 件" if disabled_count else "")
            + (f" / 失敗 {failed_count} 件" if failed_count else ""),
        ),
    )
    # CLIの終了コードだけでなく、Claude Codeが次回起動時に読む実体を完了条件とする。
    _verify_target_plugins(target_versions, failed_operations)
    return external_changed or any_change, recommendations, disable_failed_count, artifact_failed_count, cache_deferred


def _remove_unused_plugins() -> bool:
    """導入済みのcompact-plusと、そのmarketplace登録を削除する。"""
    changed = False
    installed = _get_installed_plugins_raw()
    if installed is None:
        logger.warning("compact-plusの導入状態を取得できない。update-dotfilesを再実行する")
        return False
    if "compact-plus@compact-plus" in _user_scope_plugin_ids(installed):
        result = claude_common.run_claude(
            ["plugin", "uninstall", "compact-plus@compact-plus", "--scope=user", "--keep-data"],
            timeout=claude_common.PLUGIN_OPERATION_TIMEOUT,
        )
        if result is None or result.returncode != 0:
            logger.warning("compact-plusの削除に失敗: %s。update-dotfilesを再実行する", common.format_cli_error(result))
            return False
        changed = True
    marketplaces = _get_marketplaces_raw()
    if marketplaces is None:
        logger.warning("compact-plusのmarketplace登録を取得できない。update-dotfilesを再実行する")
        return changed
    if _marketplace_entry(marketplaces, "compact-plus") is not None:
        result = claude_common.run_claude(
            ["plugin", "marketplace", "remove", "compact-plus"], timeout=claude_common.PLUGIN_OPERATION_TIMEOUT
        )
        if result is None or result.returncode != 0:
            logger.warning(
                "compact-plusのmarketplace削除に失敗: %s。update-dotfilesを再実行する", common.format_cli_error(result)
            )
        else:
            changed = True
    return changed


def _get_marketplaces_raw() -> object | None:
    """`claude plugin marketplace list --json`の生パース結果を返す。"""
    result = claude_common.run_claude(
        ["plugin", "marketplace", "list", "--json"], timeout=claude_common.PLUGIN_OPERATION_TIMEOUT
    )
    if result is None or result.returncode != 0:
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return None


def _marketplace_entry(data: object, marketplace_name: str) -> dict[str, object] | None:
    """指定したマーケットプレイス名の一覧要素を返す。"""
    if isinstance(data, dict):
        dict_data = cast("dict[str, object]", data)
        if "marketplaces" in dict_data:
            return _marketplace_entry(dict_data["marketplaces"], marketplace_name)
    if isinstance(data, list):
        for item in cast("list[object]", data):
            if isinstance(item, dict):
                entry = cast("dict[str, object]", item)
                if entry.get("name") == marketplace_name:
                    return entry
    return None


def compute_recommended_commands(raw_data: object, enabled_map: dict[str, bool] | None) -> list[str]:
    """現状と ``_AUTO_ENABLED_PLUGIN_IDS`` の乖離を ``claude plugin ...`` 提案列で返す。

    - ``_AUTO_ENABLED_PLUGIN_IDS`` のうち未インストール → ``claude plugin install <id> --scope=user``
    - ``_AUTO_ENABLED_PLUGIN_IDS`` のうちインストール済みかつ ``enabledPlugins[id]`` が ``false``
      → ``claude plugin enable <id> --scope=user``

    ``_AUTO_DISABLED_PLUGIN_IDS`` のdisable実行は ``_auto_disable_plugins()`` が直接行うため、
    本関数では推奨コマンドの算出対象に含めない。

    順序は推奨理由ごとにinstall → enableとし、同カテゴリ内はIDで昇順にソートする
    （出力の安定化と視認性のため）。
    """
    installed_ids = _user_scope_plugin_ids(raw_data)
    install_cmds: list[str] = []
    enable_cmds: list[str] = []
    for plugin_id in sorted(_AUTO_ENABLED_PLUGIN_IDS):
        if plugin_id not in installed_ids:
            install_cmds.append(f"claude plugin install {plugin_id} --scope=user")
        elif enabled_map is not None and enabled_map.get(plugin_id) is False:
            enable_cmds.append(f"claude plugin enable {plugin_id} --scope=user")
    return install_cmds + enable_cmds


def _auto_disable_plugins(raw_data: object, enabled_map: dict[str, bool] | None) -> tuple[int, int]:
    """``_AUTO_DISABLED_PLUGIN_IDS`` のうち有効状態のものを自動で無効化する。

    インストール済みかつ ``enabledPlugins[id]`` が ``false`` でない（設定が無ければ有効として扱う）対象に対し、
    ``claude plugin disable <id> --scope=user`` を発行する。失敗しても他対象を続行する。

    Returns:
        (成功件数, 失敗件数) のタプル。
    """
    installed_ids = _user_scope_plugin_ids(raw_data)
    disabled = 0
    failed = 0
    for plugin_id in sorted(_AUTO_DISABLED_PLUGIN_IDS):
        if plugin_id not in installed_ids:
            continue
        if enabled_map is not None and enabled_map.get(plugin_id) is False:
            continue
        if _disable_plugin(plugin_id):
            disabled += 1
        else:
            failed += 1
    return disabled, failed


def _user_scope_plugin_ids(raw_data: object) -> set[str]:
    """`claude plugin list` のraw dataからuser scopeの ``id`` 集合を返す。

    自動無効化・自動有効化の判定で `<plugin>@<marketplace>` 形式の `id` を
    そのまま参照したいケース向け（既存の `_extract_plugin_version_map` は
    ak110-dotfiles marketplaceの `name` のみを返すため別関数にする）。
    """
    if not isinstance(raw_data, list):
        return set()
    ids: set[str] = set()
    for item in cast("list[object]", raw_data):
        if not isinstance(item, dict):
            continue
        entry = cast("dict[str, object]", item)
        if entry.get("scope") not in (None, "user"):
            continue
        plugin_id = entry.get("id")
        if isinstance(plugin_id, str):
            ids.add(plugin_id)
    return ids


def _read_installed_plugins_from_file() -> list[dict[str, object]] | None:
    """`installed_plugins.json`を直接読み取り、CLI互換のlist[dict]形式に変換する。

    ファイルの形式:
        {"version": 2, "plugins": {"name@marketplace": [{"scope": "user", "version": "0.15.0", ...}]}}

    CLI出力互換の形式に変換:
        [{"id": "name@marketplace", "scope": "user", "version": "0.15.0", ...}]

    読み取り失敗時はNoneを返し、呼び出し元でCLIフォールバックさせる。
    """
    try:
        data = json.loads(claude_common.INSTALLED_PLUGINS_PATH.read_text(encoding="utf-8"))
    except OSError, json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    plugins = data.get("plugins")
    if not isinstance(plugins, dict):
        return None
    result: list[dict[str, object]] = []
    for key, entries in plugins.items():
        if not isinstance(key, str) or not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            item: dict[str, object] = {"id": key}
            for field in ("scope", "version", "projectPath"):
                if field in entry:
                    item[field] = entry[field]
            result.append(item)
    return result


def _prerequisites_ok() -> bool:
    """前提条件 (claude と uv の両方が PATH にあるか) を確認する。"""
    if common.resolve_executable("claude", preferred_directories=(Path.home() / ".local" / "bin",)) is None:
        logger.info(
            log_format.format_status(
                "plugins",
                "claude CLI 未検出のためスキップ。`update-dotfiles`を再実行してClaude Codeを導入する",
            ),
        )
        return False
    if common.resolve_executable("uv", preferred_directories=(Path.home() / ".local" / "bin",)) is None:
        logger.info(
            log_format.format_status(
                "plugins",
                "uv CLI 未検出のためスキップ (plugin hook はuvプロジェクトを使う)。"
                "https://docs.astral.sh/uv/getting-started/installation/ "
                "の手順でuvを導入して`update-dotfiles`を再実行する",
            ),
        )
        return False
    return True


def _get_installed_plugins_raw() -> object | None:
    """`claude plugin list --json` の生パース結果を返す。失敗時は None。"""
    result = claude_common.run_claude(["plugin", "list", "--json"])
    if result is None or result.returncode != 0:
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        logger.info(
            log_format.format_status(
                "plugins",
                "`claude plugin list --json` の出力 JSON を解析できませんでした。"
                "`claude plugin list --json`の出力を確認して`update-dotfiles`を再実行する",
            ),
        )
        return None


def _extract_plugin_version_map(data: object) -> dict[str, str]:
    """`claude plugin list --json` の戻り値からuser scopeのname → version辞書を構築する。

    本スクリプトは ``--scope=user`` でインストールするため、
    user scopeのエントリのみを対象とする。``scope`` フィールドが存在しない
    エントリは後方互換のため含める。

    実機で確認した形式（Claude Code 2.x）: list[dict]で各要素が以下を持つ。

    - `id`: `<plugin>@<marketplace>` 形式（例: `agent-toolkit@ak110-dotfiles`）
    - `version`: 例 `"0.1.0"`（ない場合は空文字列として記録）
    - `scope`: `"user"` / `"project"` / `"local"` 等
    - `name`: ない場合あり

    対応する全形式:

    - list[dict]: 各要素の `id`（`@` の前を切り出す）または `name` フィールドをplugin名とする
    - dict[str, ...]: キーがplugin名（versionは不明として空文字列）
    - dictに `plugins` キーがあり、その中に上記いずれか

    未知の形式では空辞書を返す（呼び出し側でスキップ扱いになる）。
    """
    if isinstance(data, dict):
        dict_data = cast("dict[str, object]", data)
        if "plugins" in dict_data:
            return _extract_plugin_version_map(dict_data["plugins"])
        return {key: "" for key in dict_data if isinstance(key, str)}
    if isinstance(data, list):
        list_data = cast("list[object]", data)
        versions: dict[str, str] = {}
        for item in list_data:
            if isinstance(item, dict):
                item_dict = cast("dict[str, object]", item)
                # user scope 以外のエントリは管理対象外
                if item_dict.get("scope") not in (None, "user"):
                    continue
                name = _name_from_entry(item_dict)
                if name is not None:
                    raw_version = item_dict.get("version")
                    versions[name] = raw_version if isinstance(raw_version, str) else ""
        return versions
    return {}


def _name_from_entry(entry: dict[str, object]) -> str | None:
    """`plugin list` の1エントリからplugin名を取り出す（優先順位: `id` の `@` 前 → `name`）。"""
    raw_id = entry.get("id")
    if isinstance(raw_id, str):
        # `id` は `<name>@<marketplace>` 形式。`@` がなければそのまま返す
        return raw_id.split("@", 1)[0]
    name = entry.get("name")
    if isinstance(name, str):
        return name
    return None


def _read_target_info(dotfiles_root: Path) -> tuple[dict[str, str], set[str]]:
    """`marketplace.json` から目標バージョン辞書とdeprecated名の集合を返す。

    ``keywords`` に ``"deprecated"`` を含むエントリはdeprecated扱いとし、
    通常のインストール・更新対象から除外する。

    読み込み失敗時は空辞書・空集合を返す（更新判定をスキップする）。
    """
    manifest = dotfiles_root / ".claude-plugin" / "marketplace.json"
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        logger.info(
            log_format.format_status(
                "plugins",
                f"marketplace.json の読み込みに失敗: {e}。"
                f"{manifest}の読み取り権限とJSONの書式を修正して`update-dotfiles`を再実行する",
            ),
        )
        return {}, set()
    plugins = data.get("plugins") if isinstance(data, dict) else None
    if not isinstance(plugins, list):
        return {}, set()
    targets: dict[str, str] = {}
    deprecated: set[str] = set()
    for entry in plugins:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        version = entry.get("version")
        if not isinstance(name, str) or not isinstance(version, str):
            continue
        keywords = entry.get("keywords")
        if isinstance(keywords, list) and "deprecated" in keywords:
            deprecated.add(name)
        else:
            targets[name] = version
    return targets, deprecated


def _is_installed(name: str, raw_data: object) -> bool:
    """指定プラグインが何らかのスコープでインストール済みか判定する。"""
    if not isinstance(raw_data, list):
        return False
    for item in cast("list[object]", raw_data):
        if isinstance(item, dict) and _name_from_entry(cast("dict[str, object]", item)) == name:
            return True
    return False


def _project_scope_paths(name: str, raw_data: object) -> list[Path]:
    """指定プラグインのproject scopeエントリに対応するprojectPath一覧を返す。

    `claude plugin uninstall --scope=project` は呼び出し時のcwdに紐づく
    プロジェクト設定のみを参照する。そのため、除去対象のproject scope
    エントリごとにインストール元プロジェクトの絶対パスを取得し、
    後段でそれをcwdにしてCLIを呼ぶ必要がある。
    """
    if not isinstance(raw_data, list):
        return []
    paths: list[Path] = []
    for item in cast("list[object]", raw_data):
        if not isinstance(item, dict):
            continue
        entry = cast("dict[str, object]", item)
        if _name_from_entry(entry) != name or entry.get("scope") != "project":
            continue
        project_path = entry.get("projectPath")
        if not isinstance(project_path, str) or not project_path:
            logger.info(
                log_format.format_status(
                    name,
                    "project scope エントリに projectPath が無いためスキップ。"
                    "~/.claude/plugins/installed_plugins.jsonでこのpluginの不要なproject "
                    "scopeエントリを手動削除する",
                ),
            )
            continue
        paths.append(Path(project_path))
    return paths


def _uninstall_deprecated(name: str, raw_data: object) -> bool:
    """Deprecated プラグインがインストール済みならアンインストールする。

    `claude plugin uninstall`は導入先の実体を削除せず孤立として残し、永続データ領域（`plugins/data/<id>/`）を削除する
    （Claude Code 2.1.303の隔離環境での観測）。稼働中のセッションのhook・MCPが使い続けるデータ領域を削除しないよう、
    `--keep-data`で残す。
    """
    if not _is_installed(name, raw_data):
        return False
    result = claude_common.run_claude(["plugin", "uninstall", f"{name}@{claude_common.MARKETPLACE_NAME}", "--keep-data"])
    if result is not None and result.returncode == 0:
        logger.info(log_format.format_status(name, "deprecated のためアンインストールしました"))
        return True
    logger.info(
        log_format.format_status(
            name,
            f"アンインストールに失敗: {common.format_cli_error(result)}。"
            f"`claude plugin uninstall {name}@{claude_common.MARKETPLACE_NAME} --keep-data`を実行する",
        ),
    )
    return False


def _cleanup_old_project_scope(name: str, raw_data: object) -> None:
    """管理対象プラグインの project scope エントリを除去する (user scope 移行用)。

    `claude plugin uninstall --scope=project` は呼び出し時の cwd から
    プロジェクト設定を特定するため、各エントリの projectPath を cwd に渡す。
    他の scope が残らない除去は永続データ領域を削除するため、続けて導入する user scope が同じデータ領域を
    使えるよう`--keep-data`で残す。
    """
    for project_path in _project_scope_paths(name, raw_data):
        if not project_path.is_dir():
            logger.info(
                log_format.format_status(
                    name,
                    f"project scope の除去対象 {project_path} が存在しないためスキップ "
                    "(必要に応じて ~/.claude/plugins/installed_plugins.json から手動で削除)",
                ),
            )
            continue
        result = claude_common.run_claude(
            ["plugin", "uninstall", f"{name}@{claude_common.MARKETPLACE_NAME}", "--scope=project", "--keep-data"],
            cwd=project_path,
        )
        if result is not None and result.returncode == 0:
            logger.info(log_format.format_status(name, f"project scope を除去しました ({project_path})"))
        else:
            logger.info(
                log_format.format_status(
                    name,
                    f"project scope の除去に失敗 (続行): {common.format_cli_error(result)}。"
                    f"{project_path}へ移動して`claude plugin uninstall "
                    f"{name}@{claude_common.MARKETPLACE_NAME} --scope=project --keep-data`を実行する",
                ),
            )


def _install_plugin(name: str) -> bool:
    """指定 plugin をインストールする (成功時 True を返す)。"""
    result = claude_common.run_claude(
        ["plugin", "install", f"{name}@{claude_common.MARKETPLACE_NAME}", "--scope=user", "-y"],
        timeout=claude_common.PLUGIN_OPERATION_TIMEOUT,
    )
    if result is None or result.returncode != 0:
        logger.info(
            log_format.format_status(
                name,
                f"install に失敗: {common.format_cli_error(result)}。"
                f"`claude plugin install {name}@{claude_common.MARKETPLACE_NAME} "
                "--scope=user`を実行する",
            ),
        )
        return False
    logger.info(log_format.format_status(name, "インストールしました"))
    return True


def _update_plugin(name: str) -> bool:
    """指定 plugin を最新版へ更新する (成功時 True を返す)。"""
    result = claude_common.run_claude(
        ["plugin", "update", f"{name}@{claude_common.MARKETPLACE_NAME}", "--scope=user"],
        timeout=claude_common.PLUGIN_OPERATION_TIMEOUT,
    )
    if result is None or result.returncode != 0:
        logger.info(
            log_format.format_status(
                name,
                f"update に失敗: {common.format_cli_error(result)}。"
                f"`claude plugin update {name}@{claude_common.MARKETPLACE_NAME} "
                "--scope=user`を実行する",
            ),
        )
        return False
    logger.info(log_format.format_status(name, "更新しました"))
    return True


def _disable_plugin(plugin_id: str) -> bool:
    """指定 plugin を user scope で無効化する (成功時 True を返す)。

    ``plugin_id`` は ``<name>@<marketplace>`` 形式。
    """
    result = claude_common.run_claude(["plugin", "disable", plugin_id, "--scope=user"])
    if result is None or result.returncode != 0:
        logger.info(
            log_format.format_status(
                plugin_id,
                f"disable に失敗: {common.format_cli_error(result)}。"
                f"`claude plugin disable {plugin_id} --scope=user`を実行する",
            ),
        )
        return False
    logger.info(log_format.format_status(plugin_id, "推奨設定に従って無効化しました"))
    return True


def _read_enabled_plugins_from_file() -> dict[str, bool] | None:
    """`settings.json` の `enabledPlugins` を直読みして `<id> -> bool` 辞書で返す。

    ファイル不在・解析失敗・`enabledPlugins` が非dictの場合は、有効・無効の状態を
    実体から取得できなかったことを表す `None` を返す。`None` の扱いは呼び出し元ごとに異なり、
    自動無効化と推奨コマンド算出は有効とみなし、install後の状態検証は未確認として失敗扱いとする。
    """
    data = common.load_json_dict(claude_common.SETTINGS_JSON_PATH)
    if data is None:
        return None
    enabled = data.get("enabledPlugins")
    if not isinstance(enabled, dict):
        return None
    result: dict[str, bool] = {}
    for key, value in cast("dict[str, object]", enabled).items():
        if isinstance(key, str) and isinstance(value, bool):
            result[key] = value
    return result


def _plugin_cache_directory(name: str) -> Path | None:
    """`installed_plugins.json` が保持する user scope の導入先ディレクトリを返す。"""
    try:
        data = json.loads(claude_common.INSTALLED_PLUGINS_PATH.read_text(encoding="utf-8"))
    except OSError, json.JSONDecodeError:
        return None
    plugins = data.get("plugins") if isinstance(data, dict) else None
    entries = plugins.get(f"{name}@{claude_common.MARKETPLACE_NAME}") if isinstance(plugins, dict) else None
    if not isinstance(entries, list):
        return None
    for entry in entries:
        if not isinstance(entry, dict) or entry.get("scope") not in (None, "user"):
            continue
        install_path = entry.get("installPath")
        if isinstance(install_path, str) and install_path:
            return Path(install_path).expanduser()
    return None


def _missing_plugin_cache_files(cache_dir: Path) -> list[str]:
    """必須ファイルのうち plugin cache に揃っていないものを列挙する。"""
    return [relative for relative in _PLUGIN_CACHE_REQUIRED_FILES if not (cache_dir / relative).is_file()]


def _ensure_plugin_cache_complete(name: str) -> Literal["unchanged", "repaired", "deferred"]:
    """導入先の実体が揃っていることを確認し、欠落を 1 回だけ修復する。

    修復した場合は`repaired`、稼働中の参照を守るため修復を延期した場合は`deferred`、それ以外は`unchanged`を返す。

    `claude plugin install/update` は同じ version の plugin を既に導入済みと判定した場合に
    キャッシュを検証せずスキップするため、CLI の終了コード 0 はキャッシュの完全性を含意しない。
    必須ファイルを欠いたまま後続の工程が `uv run --project <キャッシュ>` を実行すると、
    そのディレクトリがプロジェクトとして解決されず `ModuleNotFoundError` で終わる。

    導入先を解決できない場合と、導入先が plugin cache の外にある場合は、検査できなかったことと
    探索先を警告して`unchanged`を返す。導入そのものの欠落は `_verify_target_plugins` が扱うため、
    本関数では新しい失敗経路を増やさない。

    修復は導入先を削除してから再導入するため、削除の間は導入先の他のファイルへも到達できなくなる。
    このため導入先を参照するプロセスか稼働中のClaude Codeセッション（起動時刻を問わない）があれば、
    `claude_plugin_references`の判定に従って削除せず延期し、参照の終了後の`update-dotfiles`で修復する。
    """
    cache_root = claude_common.INSTALLED_PLUGINS_PATH.parent / "cache"
    cache_dir = _plugin_cache_directory(name)
    if cache_dir is None:
        source = log_format.home_short(claude_common.INSTALLED_PLUGINS_PATH)
        logger.warning(
            log_format.format_status(
                name,
                f"plugin cache の導入先を {source} から解決できず検査を省略。"
                "`claude plugin list --json`でこのpluginの導入状態を確認して`update-dotfiles`を再実行する",
            ),
        )
        return "unchanged"
    if not cache_dir.is_relative_to(cache_root):
        # 削除の対象は plugin cache の内側だけとし、想定外の位置にある導入先は削除しない。
        short = log_format.home_short(cache_dir)
        logger.info(
            log_format.format_status(
                name,
                f"plugin cache の外にある導入先のため検査を省略: {short}。配置を保持したため対応不要（処理は継続した）",
            ),
        )
        return "unchanged"
    missing = _missing_plugin_cache_files(cache_dir)
    if not missing:
        return "unchanged"
    reasons = claude_plugin_references.holding_reasons(claude_plugin_references.scan(), cache_dir, session_window=None)
    if reasons:
        logger.warning(
            log_format.format_status(
                name,
                f"plugin cache に {', '.join(missing)} がありませんが、稼働中の参照があるため修復を延期しました"
                f"（{'、'.join(reasons)}）。導入先 {log_format.home_short(cache_dir)} を削除せず保持しています。"
                + deferred_updates.retry_guidance(
                    "Claude Codeのセッションを全て終了してから`update-dotfiles`を再実行してください。"
                ),
            ),
        )
        return "deferred"
    logger.info(
        log_format.format_status(
            name,
            f"plugin cache に {', '.join(missing)} が無いため削除して再インストールします: "
            f"{log_format.home_short(cache_dir)}。"
            "対応不要（処理は継続した）",
        ),
    )
    shutil.rmtree(cache_dir, ignore_errors=True)
    if not _install_plugin(name):
        message = f"plugin cache の修復インストールに失敗しました: {log_format.home_short(cache_dir)}"
        raise RuntimeError(f"{name}: {message}")
    repaired_dir = _plugin_cache_directory(name) or cache_dir
    missing = _missing_plugin_cache_files(repaired_dir)
    if missing:
        command = f"claude plugin install {name}@{claude_common.MARKETPLACE_NAME} --scope=user -y"
        message = (
            f"再インストール後も plugin cache に {', '.join(missing)} がありません: "
            f"{log_format.home_short(repaired_dir)} (確認手順: このディレクトリを削除して `{command}` を実行する)"
        )
        raise RuntimeError(f"{name}: {message}")
    logger.info(log_format.format_status(name, "plugin cache の欠落を再インストールで解消しました"))
    return "repaired"


def _remove_development_artifacts(name: str) -> tuple[bool, int, bool]:
    """導入先の版ディレクトリから、主作業ツリー由来の開発用生成物を削除する。

    戻り値は（1件以上削除したか、削除に失敗した件数、参照中のため保持したか）。
    削除はplugin cacheの内側にある導入先の直下で、symlinkでないディレクトリに限る。
    導入先を解決できない場合とcacheの外の場合は`_ensure_plugin_cache_complete`が報告するため、ここでは何もしない。
    """
    cache_root = claude_common.INSTALLED_PLUGINS_PATH.parent / "cache"
    cache_dir = _plugin_cache_directory(name)
    if cache_dir is None or not cache_dir.is_relative_to(cache_root):
        return False, 0, False
    targets = [
        cache_dir / artifact
        for artifact in _PLUGIN_CACHE_DEVELOPMENT_ARTIFACTS
        if (cache_dir / artifact).is_dir() and not (cache_dir / artifact).is_symlink()
    ]
    if not targets:
        return False, 0, False
    processes = claude_plugin_references.scan()
    # セッションは版ディレクトリのhook・skillを使うが開発用生成物は使わないため、
    # 稼働中のセッションを保持の理由にしない（空の期間を渡す）。生成物のパスを参照するプロセスと、
    # 参照先を取得できないClaude Codeのプロセスがある間は保持する。
    now = time.time()
    removed = False
    failed = 0
    held = False
    for target in targets:
        short = log_format.home_short(target)
        reasons = claude_plugin_references.holding_reasons(processes, target, session_window=(now, now))
        if reasons:
            held = True
            logger.warning(
                log_format.format_status(
                    name,
                    f"plugin cache の開発用生成物 {short} を参照中のため削除を延期しました（{'、'.join(reasons)}）。"
                    + deferred_updates.retry_guidance("参照の終了後に`update-dotfiles`を再実行してください。"),
                ),
            )
            continue
        try:
            shutil.rmtree(target)
        except OSError as error:
            failed += 1
            logger.warning(
                log_format.format_status(
                    name,
                    f"plugin cache の開発用生成物 {short} を削除できません: {error}。"
                    "権限と使用中のプロセスを確認して`update-dotfiles`を再実行する",
                ),
            )
            continue
        removed = True
        logger.info(log_format.format_status(name, f"plugin cache の開発用生成物を削除しました: {short}"))
    return removed, failed, held


def _verify_target_plugins(target_versions: dict[str, str], failed_operations: Sequence[str] = ()) -> None:
    """管理対象pluginが目標の版でuser scopeへ導入され、有効であることを実体から検証する。

    `failed_operations`は同じ回に失敗した`claude plugin install`・`update`（`<plugin名> (<操作>)`）で、
    実体の検証結果と併せて1つの例外へまとめる。CLIが成功終了しても版が変わらない場合は版の不一致で検出する。
    """
    raw_data: object = _read_installed_plugins_from_file()
    if raw_data is None:
        raw_data = _get_installed_plugins_raw()
    if raw_data is None:
        raise RuntimeError("install後のplugin一覧を取得できませんでした")
    installed = _extract_plugin_version_map(raw_data)
    missing = sorted(name for name in target_versions if name not in installed)
    enabled = _read_enabled_plugins_from_file()
    disabled = sorted(
        f"{name}@{claude_common.MARKETPLACE_NAME}"
        for name in target_versions
        if enabled is None or enabled.get(f"{name}@{claude_common.MARKETPLACE_NAME}") is not True
    )
    mismatched = sorted(
        f"{name} ({installed[name]} != {target})"
        for name, target in target_versions.items()
        if target and name in installed and installed[name] != target
    )
    failures: list[str] = []
    if failed_operations:
        failures.append(f"CLI失敗: {', '.join(failed_operations)}")
    if missing:
        failures.append(f"未インストール: {', '.join(missing)}")
    if mismatched:
        failures.append(f"版不一致: {', '.join(mismatched)}")
    if disabled:
        failures.append(f"未有効化: {', '.join(disabled)}")
    if failures:
        message = " / ".join(failures)
        raise RuntimeError(f"Claude Code pluginのinstall後の状態検証に失敗しました: {message}")
    logger.info(log_format.format_status("plugins", "install後の導入・有効化状態を確認しました"))

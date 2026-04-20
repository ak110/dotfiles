"""dotfiles 同梱の Claude Code plugin を自動インストール/更新する。

本モジュールは `chezmoi apply` 後処理 (`pytools.post_apply`) から呼ばれる。
必要な前提条件をすべて満たす場合だけ動作し、満たさない場合は
完全にスキップする (dotfiles apply 全体を失敗させないための安全側動作)。

前提条件 (すべて必要):

1. `claude` CLI が PATH にある
2. `uv` CLI が PATH にある (plugin の hook スクリプトが `uv run --script` で動くため)

対象プラグインは `.claude-plugin/marketplace.json` の `plugins[]` 全件。
このファイルを SSOT として扱うため、`plugins/` 配下に新しいプラグインを追加して
`marketplace.json` に登録するだけで、本モジュールの対象に自動で追加される。
`keywords` に ``"deprecated"`` を含むエントリは、インストール済みであれば
自動でアンインストールされる。

前提を満たした場合の処理:

- marketplace 未登録 → `claude plugin marketplace add <dotfiles 絶対パス> --scope user` で登録
  (directory 型登録。dotfiles リポジトリを直接参照することで push/update サイクル不要で
   編集内容が反映される)
- 旧 GitHub 型登録が残存 → directory 型へ自動マイグレーション (claude_marketplace が担う)
- deprecated plugin がインストール済み → 検出されたスコープごとにアンインストール
- 管理対象 plugin が project scope に残存 → アンインストール (user scope 移行用)
- 対象 plugin が未インストール → `claude plugin install <name>@<marketplace> --scope user`
- 対象 plugin がインストール済み + version が乖離
  → `claude plugin marketplace update <name>` で marketplace メタデータを更新した後、
     `claude plugin update <name>@<marketplace> --scope user` で反映。
     この分岐は directory 型・旧 GitHub 型を区別せず適用され、version 乖離があれば
     update 経路が優先される
- 対象 plugin がインストール済み + version が一致 + directory 型登録が健全
  → `claude plugin install <name>@<marketplace> --scope user` を毎回再実行して
     キャッシュを最新化する (directory 型では `plugin update` が version 一致時 no-op
     になるため同期経路として使えない)
- 対象 plugin がインストール済み + version が一致 + 旧 GitHub 型が残存
  → install コマンドを呼ばずスキップ (マイグレーション前の旧挙動)

公式 marketplace (``claude-plugins-official``) のプラグインのうち、ユーザーが
使わないものは ``_AUTO_DISABLED_PLUGIN_IDS``、常時有効化したいものは
``_AUTO_ENABLED_PLUGIN_IDS`` に列挙している。これらの有効化・無効化・導入は
ユーザーの裁量に委ねる方針とし、``run()`` 末尾で現状と乖離のあるものだけを
``compute_recommended_commands()`` で推奨コマンド列として算出する。
呼び出し元 (``post_apply._main``) は ``consume_recommendations()`` 経由で
推奨コマンドを取り出し、利用者に案内として表示する。

本スクリプトは dotfiles リポジトリの user scope でプラグインを管理する。
他プロジェクトへ配布するときも利用者が手動で `claude plugin install ... --scope user`
することを推奨する (詳細は docs/guide/claude-code-guide.md)。

directory 型環境では version 乖離によらず毎回 `plugin install` で同期するため、
ユーザーが dotfiles で編集した内容が chezmoi apply 後に反映される。
"""

import json
import logging
import shutil
from pathlib import Path
from typing import cast

from pytools._internal import claude_common, claude_marketplace, log_format
from pytools._internal.cli import setup_logging

logger = logging.getLogger(__name__)

# claude_common から再エクスポート (後方互換・テストのpatch先として維持)
_MARKETPLACE_NAME = claude_common.MARKETPLACE_NAME
_INSTALLED_PLUGINS_PATH = claude_common.INSTALLED_PLUGINS_PATH

# 推奨的に無効化したいプラグイン (ユーザーが使わないもの)。
# 現状と乖離があれば `claude plugin disable` の提案として案内に表示する。
_AUTO_DISABLED_PLUGIN_IDS: frozenset[str] = frozenset(
    {
        "serena@claude-plugins-official",
        "superpowers@claude-plugins-official",
        "security-guidance@claude-plugins-official",
        "pr-review-toolkit@claude-plugins-official",
        "code-simplifier@claude-plugins-official",
        "commit-commands@claude-plugins-official",
        "code-review@claude-plugins-official",
    }
)

# 推奨的にインストール+有効化したいプラグイン。
# 未インストールなら `claude plugin install`、明示的に false なら
# `claude plugin enable` の提案として案内に表示する。
_AUTO_ENABLED_PLUGIN_IDS: frozenset[str] = frozenset(
    {
        "context7@claude-plugins-official",
        "typescript-lsp@claude-plugins-official",
        "claude-md-management@claude-plugins-official",
        "skill-creator@claude-plugins-official",
    }
)

# 直近の `run()` 呼び出しで算出された推奨コマンド列。
# `post_apply._main` が `consume_recommendations()` 経由で取り出して案内表示する。
_LAST_RECOMMENDATIONS: list[str] = []


def _read_installed_plugins_from_file() -> list[dict[str, object]] | None:
    """installed_plugins.jsonを直接読み取り、CLI互換のlist[dict]形式に変換する。

    ファイルの形式:
        {"version": 2, "plugins": {"name@marketplace": [{"scope": "user", "version": "0.15.0", ...}]}}

    CLI出力互換の形式に変換:
        [{"id": "name@marketplace", "scope": "user", "version": "0.15.0", ...}]

    読み取り失敗時はNoneを返し、呼び出し元でCLIフォールバックさせる。
    """
    try:
        data = json.loads(_INSTALLED_PLUGINS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
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


def _main() -> None:
    """スタンドアロン実行用エントリポイント。"""
    setup_logging()
    run()


def run() -> bool:
    """Claude Code plugin をインストール/更新する。

    Returns:
        何らかの plugin を新たにインストールまたは更新したら True。
    """
    # post_apply へ推奨コマンドを受け渡すための意図的なモジュール状態
    global _LAST_RECOMMENDATIONS  # noqa: PLW0603 # pylint: disable=global-statement
    _LAST_RECOMMENDATIONS = []
    if not _prerequisites_ok():
        return False

    dotfiles_root = _find_dotfiles_root()
    if dotfiles_root is None:
        logger.info(log_format.format_status("plugins", "dotfiles ルート (marketplace.json) が見つからずスキップ"))
        return False

    # 対象プラグインは marketplace.json の plugins[] 全件から動的に決める。
    target_versions, deprecated_names = _read_target_info(dotfiles_root)
    if not target_versions and not deprecated_names:
        logger.info(log_format.format_status("plugins", "marketplace.json に対象 plugin が無いためスキップ"))
        return False

    # ファイル直接読み取りを先に試み、失敗時のみCLIフォールバックする
    raw_data: object = _read_installed_plugins_from_file()
    if raw_data is None:
        raw_data = _get_installed_plugins_raw()
        if raw_data is None:
            logger.info(log_format.format_status("plugins", "インストール済み plugin 一覧の取得に失敗したためスキップ"))
            return False

    if not claude_marketplace.ensure_marketplace():
        return False

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
    failed_count = 0
    for name, target in target_versions.items():
        # project scope に残存するエントリを除去 (user scope 移行用)
        _cleanup_old_project_scope(name, raw_data)

        current = installed.get(name)
        if current is None:
            if _install_plugin(name):
                any_change = True
                installed_count += 1
            else:
                failed_count += 1
        elif target and current != target:
            logger.info(log_format.format_status(name, f"更新を検出: {current} -> {target}"))
            if _update_plugin(name):
                any_change = True
                updated_count += 1
            else:
                failed_count += 1
        elif is_directory_type:
            # directory 型登録が健全かつ version 一致の場合、dotfiles 側の編集を反映するため
            # `plugin install` を再実行してキャッシュを最新化する。
            logger.info(log_format.format_status(name, f"最新 ({current or '不明'}) — directory 型キャッシュ再同期"))
            if _install_plugin(name):
                any_change = True
                resynced_count += 1
            else:
                failed_count += 1
        else:
            logger.info(log_format.format_status(name, f"最新 ({current or '不明'})"))
            latest_count += 1

    # 外部 marketplace のプラグイン推奨コマンド算出 (公式プラグインの有効化・無効化)。
    # 対象は ak110-dotfiles 以外の marketplace であり、上記のインストールループで
    # 状態が変わらないため、冒頭で取得した raw_data を再利用してよい。
    _LAST_RECOMMENDATIONS = compute_recommended_commands(raw_data, _read_enabled_plugins_from_file())

    # install 試行後のポスト検証と最終サマリ。
    # 再現性の怪しい未インストール事象を早期に検出できるよう、install 試行後に
    # installed_plugins.json を再読み込みして欠落を警告する。
    _warn_if_missing(target_versions)
    logger.info(
        log_format.format_status(
            "plugins",
            f"サマリ: 最新 {latest_count} 件 / 更新 {updated_count} 件 / 新規 {installed_count} 件"
            + (f" / 再同期 {resynced_count} 件" if resynced_count else "")
            + (f" / 失敗 {failed_count} 件" if failed_count else ""),
        )
    )
    return any_change


def _prerequisites_ok() -> bool:
    """前提条件 (claude と uv の両方が PATH にあるか) を確認する。"""
    if shutil.which("claude") is None:
        logger.info(log_format.format_status("plugins", "claude CLI 未検出のためスキップ"))
        return False
    if shutil.which("uv") is None:
        logger.info(log_format.format_status("plugins", "uv CLI 未検出のためスキップ (plugin hook は uv run --script を使う)"))
        return False
    return True


def _find_dotfiles_root() -> Path | None:
    """本ファイルから見た dotfiles ルートディレクトリを返す。

    dotfiles ルートは `.claude-plugin/marketplace.json` を持つ。
    `pytools/_internal/install_claude_plugins.py` は dotfiles/pytools/_internal/ に置かれているため
    親の親の親がルート。
    """
    candidate = Path(__file__).resolve().parent.parent.parent
    if (candidate / ".claude-plugin" / "marketplace.json").is_file():
        return candidate
    return None


def _get_installed_plugins_raw() -> object | None:
    """`claude plugin list --json` の生パース結果を返す。失敗時は None。"""
    result = claude_common.run_claude(["plugin", "list", "--json"])
    if result is None or result.returncode != 0:
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        logger.info(log_format.format_status("plugins", "`claude plugin list --json` の出力 JSON を解析できませんでした"))
        return None


def _extract_plugin_version_map(data: object) -> dict[str, str]:
    """`claude plugin list --json` の戻り値から user scope の name → version 辞書を作る。

    本スクリプトは ``--scope user`` でインストールするため、
    user scope のエントリのみを対象とする。``scope`` フィールドが存在しない
    エントリは後方互換のため含める。

    実機で確認した形式 (Claude Code 2.x): list[dict] で各要素が以下を持つ。

    - `id`: `<plugin>@<marketplace>` 形式 (例: `agent-toolkit@ak110-dotfiles`)
    - `version`: 例 `"0.1.0"` (無い場合は空文字列として記録)
    - `scope`: `"user"` / `"project"` / `"local"` 等
    - `name`: ない場合あり

    知っている全形式:

    - list[dict]: 各要素の `id` (`@` の前を切り出す) または `name` フィールドを plugin 名とする
    - dict[str, ...]: キーが plugin 名 (version は不明として空文字列)
    - dict に `plugins` キーがあり、その中に上記いずれか

    未知の形式では空辞書を返す (呼び出し側でスキップ扱いになる)。
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
    """`plugin list` の 1 エントリから plugin 名を取り出す (優先順位: `id` の `@` 前 → `name`)."""
    raw_id = entry.get("id")
    if isinstance(raw_id, str):
        # `id` は `<name>@<marketplace>` 形式。`@` がなければそのまま返す
        return raw_id.split("@", 1)[0]
    name = entry.get("name")
    if isinstance(name, str):
        return name
    return None


def _read_target_info(dotfiles_root: Path) -> tuple[dict[str, str], set[str]]:
    """`marketplace.json` から目標バージョン辞書と deprecated 名の集合を返す。

    ``keywords`` に ``"deprecated"`` を含むエントリは deprecated 扱いとし、
    通常のインストール/更新対象から除外する。

    読み込み失敗時は空辞書・空集合を返す (更新判定をスキップする方針とする)。
    """
    manifest = dotfiles_root / ".claude-plugin" / "marketplace.json"
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        logger.info(log_format.format_status("plugins", f"marketplace.json の読み込みに失敗: {e}"))
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
    """指定プラグインの project scope エントリに対応する projectPath 一覧を返す。

    `claude plugin uninstall --scope project` は呼び出し時の cwd に紐づく
    プロジェクト設定のみを参照する。そのため、除去対象の project scope
    エントリごとにインストール元プロジェクトの絶対パスを取得し、
    後段でそれを cwd にして CLI を呼ぶ必要がある。
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
            logger.info(log_format.format_status(name, "project scope エントリに projectPath が無いためスキップ"))
            continue
        paths.append(Path(project_path))
    return paths


def _uninstall_deprecated(name: str, raw_data: object) -> bool:
    """Deprecated プラグインがインストール済みならアンインストールする。"""
    if not _is_installed(name, raw_data):
        return False
    result = claude_common.run_claude(["plugin", "uninstall", f"{name}@{_MARKETPLACE_NAME}"])
    if result is not None and result.returncode == 0:
        logger.info(log_format.format_status(name, "deprecated のためアンインストールしました"))
        return True
    logger.info(log_format.format_status(name, f"アンインストールに失敗: {claude_common.format_cli_error(result)}"))
    return False


def _cleanup_old_project_scope(name: str, raw_data: object) -> None:
    """管理対象プラグインの project scope エントリを除去する (user scope 移行用)。

    `claude plugin uninstall --scope project` は呼び出し時の cwd から
    プロジェクト設定を特定するため、各エントリの projectPath を cwd に渡す。
    """
    for project_path in _project_scope_paths(name, raw_data):
        if not project_path.is_dir():
            logger.info(
                log_format.format_status(
                    name,
                    f"project scope の除去対象 {project_path} が存在しないためスキップ "
                    "(必要に応じて ~/.claude/plugins/installed_plugins.json から手動で削除)",
                )
            )
            continue
        result = claude_common.run_claude(
            ["plugin", "uninstall", f"{name}@{_MARKETPLACE_NAME}", "--scope", "project"],
            cwd=project_path,
        )
        if result is not None and result.returncode == 0:
            logger.info(log_format.format_status(name, f"project scope を除去しました ({project_path})"))
        else:
            logger.info(
                log_format.format_status(name, f"project scope の除去に失敗 (続行): {claude_common.format_cli_error(result)}")
            )


def _install_plugin(name: str) -> bool:
    """指定 plugin をインストールする (成功時 True を返す)。"""
    result = claude_common.run_claude(["plugin", "install", f"{name}@{_MARKETPLACE_NAME}", "--scope", "user"])
    if result is None or result.returncode != 0:
        logger.info(log_format.format_status(name, f"install に失敗: {claude_common.format_cli_error(result)}"))
        return False
    logger.info(log_format.format_status(name, "インストールしました"))
    return True


def _update_plugin(name: str) -> bool:
    """指定 plugin を最新版へ更新する (成功時 True を返す)。"""
    result = claude_common.run_claude(["plugin", "update", f"{name}@{_MARKETPLACE_NAME}", "--scope", "user"])
    if result is None or result.returncode != 0:
        logger.info(log_format.format_status(name, f"update に失敗: {claude_common.format_cli_error(result)}"))
        return False
    logger.info(log_format.format_status(name, "更新しました"))
    return True


def compute_recommended_commands(raw_data: object, enabled_map: dict[str, bool] | None) -> list[str]:
    """現状と ``_AUTO_*_PLUGIN_IDS`` の乖離を ``claude plugin ...`` 提案列で返す。

    - ``_AUTO_ENABLED_PLUGIN_IDS`` のうち未インストール → ``claude plugin install <id> --scope user``
    - ``_AUTO_ENABLED_PLUGIN_IDS`` のうちインストール済みかつ ``enabledPlugins[id]`` が ``false``
      → ``claude plugin enable <id> --scope user``
    - ``_AUTO_DISABLED_PLUGIN_IDS`` のうちインストール済みかつ ``enabledPlugins[id]`` が ``false`` でない
      (= 既定で有効) → ``claude plugin disable <id> --scope user``

    順序は推奨理由ごとに install → enable → disable とし、同カテゴリ内は
    ID で昇順にソートする (出力の安定化とユーザー側の視認性のため)。
    """
    installed_ids = _user_scope_plugin_ids(raw_data)
    install_cmds: list[str] = []
    enable_cmds: list[str] = []
    disable_cmds: list[str] = []
    for plugin_id in sorted(_AUTO_ENABLED_PLUGIN_IDS):
        if plugin_id not in installed_ids:
            install_cmds.append(f"claude plugin install {plugin_id} --scope user")
        elif enabled_map is not None and enabled_map.get(plugin_id) is False:
            enable_cmds.append(f"claude plugin enable {plugin_id} --scope user")
    for plugin_id in sorted(_AUTO_DISABLED_PLUGIN_IDS):
        if plugin_id not in installed_ids:
            continue
        if enabled_map is not None and enabled_map.get(plugin_id) is False:
            continue
        disable_cmds.append(f"claude plugin disable {plugin_id} --scope user")
    return install_cmds + enable_cmds + disable_cmds


def consume_recommendations() -> list[str]:
    """直近の ``run()`` が算出した推奨コマンド列を取り出してクリアする。

    post_apply._main がサマリ末尾の案内表示で 1 度だけ読み取る想定。
    ``run()`` を複数回呼ぶテスト等で前回の推奨が残ることを防ぐ意図で一度取り出すと空になる。
    """
    # 取り出し後の初期化はワンショット契約の一部
    global _LAST_RECOMMENDATIONS  # noqa: PLW0603 # pylint: disable=global-statement
    recommendations = _LAST_RECOMMENDATIONS
    _LAST_RECOMMENDATIONS = []
    return recommendations


def _user_scope_plugin_ids(raw_data: object) -> set[str]:
    """`claude plugin list` の raw data から user scope の ``id`` 集合を返す。

    自動無効化・自動有効化の判定で、`<plugin>@<marketplace>` 形式の `id` を
    そのまま参照したいケース向け (既存の `_extract_plugin_version_map` は
    ak110-dotfiles marketplace の `name` のみを返すため別関数にする)。
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


def _read_enabled_plugins_from_file() -> dict[str, bool] | None:
    """`settings.json` の `enabledPlugins` を直読みして `<id> -> bool` 辞書で返す。

    ファイル不在・解析失敗・`enabledPlugins` が非 dict の場合は `None` を返し、
    呼び出し元では「情報なし」として扱う (デフォルト有効扱いと同等)。
    """
    data = claude_common.load_json_dict(claude_common.SETTINGS_JSON_PATH, silent=True)
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


def _warn_if_missing(target_versions: dict[str, str]) -> None:
    """Install 試行後も `target_versions` の未インストールが残っていれば警告する。

    再現性が不明瞭な未インストール事象の早期検出を目的とする。
    最新情報を取りたいため installed_plugins.json の再読み取りを行う。
    """
    raw_data: object = _read_installed_plugins_from_file()
    if raw_data is None:
        raw_data = _get_installed_plugins_raw()
    if raw_data is None:
        return
    installed = _extract_plugin_version_map(raw_data)
    missing = sorted(name for name in target_versions if name not in installed)
    if missing:
        logger.warning(
            log_format.format_status(
                "plugins",
                f"install 試行後も未インストールが残っています: {', '.join(missing)}",
            )
        )


if __name__ == "__main__":
    _main()

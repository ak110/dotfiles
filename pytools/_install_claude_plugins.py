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

- marketplace 未登録 → `claude plugin marketplace add <dotfiles root>` で登録
  (ローカルパスを使うのは、オフライン環境でも動くため。GitHub 経由では
   ネットワーク依存になる)
- deprecated plugin がインストール済み → 検出されたスコープごとにアンインストール
- 管理対象 plugin が project scope に残存 → アンインストール (user scope 移行用)
- 対象 plugin が未インストール → `claude plugin install <name>@<marketplace> --scope user`
- 対象 plugin がインストール済みで `marketplace.json` と version が乖離
  → `claude plugin marketplace update <name>` で marketplace メタデータを更新した後、
     `claude plugin update <name>@<marketplace> --scope user` で反映
  (version が一致していれば update コマンドは呼ばずスキップ)

本スクリプトは dotfiles リポジトリの user scope でプラグインを管理する。
他プロジェクトへ配布するときも利用者が手動で `claude plugin install ... --scope user`
することを推奨する (詳細は docs/guide/claude-code-guide.md)。

`update-dotfiles` 経由で本モジュールが実行されるたびに version 乖離が解消されるため、
ユーザー環境では marketplace.json の version 更新が自動で反映される。
"""

import contextlib
import json
import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import cast

from pytools import _log_format

logger = logging.getLogger(__name__)

# marketplace.json の `name` と一致させる (.claude-plugin/marketplace.json を参照)
_MARKETPLACE_NAME = "ak110-dotfiles"

# `claude plugin` コマンドのタイムアウト (秒)
# ローカルパスからの install は通常 1-2 秒で終わるが、念のため余裕を持たせる
_CLAUDE_TIMEOUT = 30

# Claude Code設定ファイルのパス (CLI呼び出しを回避するための直接読み取り用)
# marketplace 登録情報は known_marketplaces.json と settings.json.extraKnownMarketplaces の
# 2箇所に保存されるため、両方を点検・修復する必要がある。
_INSTALLED_PLUGINS_PATH = Path.home() / ".claude" / "plugins" / "installed_plugins.json"
_KNOWN_MARKETPLACES_PATH = Path.home() / ".claude" / "plugins" / "known_marketplaces.json"
_SETTINGS_JSON_PATH = Path.home() / ".claude" / "settings.json"


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


def _check_marketplace_from_file(dotfiles_root: Path) -> bool | None:
    """known_marketplaces.json と settings.json.extraKnownMarketplaces の両方を検査する。

    Returns:
        True: どちらも（存在する側が）健全。再登録不要。
        False: どちらかが壊れている（相対パス・パス不一致・github型など）。
        None: 両ファイルとも未登録。CLI経路で新規登録が必要。

    2ファイルを両方検査する理由:
        CLI の ``claude plugin marketplace remove``/``add`` が両ファイルを同時に
        更新しないケースがあり、settings.json 側だけに相対パスが残留して
        ``Marketplace file not found`` エラーを引き起こす環境が確認されている。
        どちらか片方でも壊れていれば修復対象とする。
    """
    expected = str(dotfiles_root)
    known = _load_known_marketplace_entry()
    extra = _load_extra_known_marketplace_entry()
    if known is None and extra is None:
        return None
    if known is not None and not _is_entry_healthy(known, expected):
        return False
    return not (extra is not None and not _is_entry_healthy(extra, expected))


def _load_known_marketplace_entry() -> dict[str, object] | None:
    """known_marketplaces.json から対象 marketplace のエントリを読み込む。"""
    data = _load_json_dict(_KNOWN_MARKETPLACES_PATH, silent=True)
    if data is None:
        return None
    entry = data.get(_MARKETPLACE_NAME)
    return cast("dict[str, object]", entry) if isinstance(entry, dict) else None


def _load_extra_known_marketplace_entry() -> dict[str, object] | None:
    """settings.json.extraKnownMarketplaces から対象 marketplace のエントリを読み込む。"""
    data = _load_json_dict(_SETTINGS_JSON_PATH, silent=True)
    if data is None:
        return None
    extra = data.get("extraKnownMarketplaces")
    if not isinstance(extra, dict):
        return None
    entry = cast("dict[str, object]", extra).get(_MARKETPLACE_NAME)
    return cast("dict[str, object]", entry) if isinstance(entry, dict) else None


def _is_entry_healthy(entry: dict[str, object], expected: str) -> bool:
    """登録エントリが期待パスと整合しているかを判定する。

    ``source.path`` と ``installLocation`` の双方を取り出し、いずれも存在すれば
    どちらも expected と一致するとき True。片方のみ存在する場合は存在する側が
    一致すれば True（settings.json の extraKnownMarketplaces は ``source.path``
    のみを持つ形式が正常なため、情報不足で False に倒さない）。
    相対パス・github 型・欠落のみのエントリは壊れたエントリとして False を返す。
    """
    candidates: list[str] = []
    source = entry.get("source")
    if isinstance(source, dict):
        inner = cast("dict[str, object]", source).get("path")
        if isinstance(inner, str):
            candidates.append(inner)
    install_location = entry.get("installLocation")
    if isinstance(install_location, str):
        candidates.append(install_location)
    if not candidates:
        return False
    return all(Path(p).is_absolute() and p == expected for p in candidates)


def _extract_entry_abs_path(entry: dict[str, object]) -> str | None:
    """エントリから絶対パス文字列を抽出する。相対パス・未設定は None。

    参照順: ``source.path``（dict 経由）→ トップレベル ``installLocation``
    → トップレベル ``path``。``update-dotfiles`` 履歴で残った相対パスを
    壊れたエントリとして検出するため、絶対パスの場合にのみ値を返す。
    """
    source = entry.get("source")
    if isinstance(source, dict):
        inner = cast("dict[str, object]", source).get("path")
        if isinstance(inner, str) and Path(inner).is_absolute():
            return inner
    for key in ("installLocation", "path"):
        value = entry.get(key)
        if isinstance(value, str) and Path(value).is_absolute():
            return value
    return None


def _main() -> None:
    """スタンドアロン実行用エントリポイント。"""
    logging.basicConfig(format="%(message)s", level="INFO")
    run()


def run() -> bool:
    """Claude Code plugin をインストール/更新する。

    Returns:
        何らかの plugin を新たにインストールまたは更新したら True。
    """
    if not _prerequisites_ok():
        return False

    dotfiles_root = _find_dotfiles_root()
    if dotfiles_root is None:
        logger.info(_log_format.format_status("plugins", "dotfiles ルート (marketplace.json) が見つからずスキップ"))
        return False

    # 対象プラグインは marketplace.json の plugins[] 全件から動的に決める。
    target_versions, deprecated_names = _read_target_info(dotfiles_root)
    if not target_versions and not deprecated_names:
        logger.info(_log_format.format_status("plugins", "marketplace.json に対象 plugin が無いためスキップ"))
        return False

    # ファイル直接読み取りを先に試み、失敗時のみCLIフォールバックする
    raw_data: object = _read_installed_plugins_from_file()
    if raw_data is None:
        raw_data = _get_installed_plugins_raw()
        if raw_data is None:
            logger.info(_log_format.format_status("plugins", "インストール済み plugin 一覧の取得に失敗したためスキップ"))
            return False

    if not _ensure_marketplace(dotfiles_root):
        return False

    any_change = False

    # deprecated プラグインをアンインストール
    for name in deprecated_names:
        if _uninstall_deprecated(name, raw_data):
            any_change = True

    # user scope のバージョン辞書を取得
    installed = _extract_plugin_version_map(raw_data)

    # インストール済みプラグインにバージョン不一致があるときのみmarketplaceメタデータを
    # refreshする。全て最新なら不要（CLIの起動コストを節約）。
    # 新規installしかない場合もinstallコマンドが毎回ファイルを読むためrefresh不要。
    if any(name in installed and installed[name] != target for name, target in target_versions.items() if target):
        _refresh_marketplace()

    for name, target in target_versions.items():
        # project scope に残存するエントリを除去 (user scope 移行用)
        _cleanup_old_project_scope(name, raw_data)

        current = installed.get(name)
        if current is None:
            if _install_plugin(name):
                any_change = True
        elif target and current != target:
            logger.info(_log_format.format_status(name, f"更新を検出: {current} -> {target}"))
            if _update_plugin(name):
                any_change = True
        else:
            logger.info(_log_format.format_status(name, f"最新 ({current or '不明'})"))
    return any_change


def _prerequisites_ok() -> bool:
    """前提条件 (claude と uv の両方が PATH にあるか) を確認する。"""
    if shutil.which("claude") is None:
        logger.info(_log_format.format_status("plugins", "claude CLI 未検出のためスキップ"))
        return False
    if shutil.which("uv") is None:
        logger.info(_log_format.format_status("plugins", "uv CLI 未検出のためスキップ (plugin hook は uv run --script を使う)"))
        return False
    return True


def _find_dotfiles_root() -> Path | None:
    """本ファイルから見た dotfiles ルートディレクトリを返す。

    dotfiles ルートは `.claude-plugin/marketplace.json` を持つ。
    `pytools/install_claude_plugins.py` は dotfiles/pytools/ に置かれているため
    親の親がルート。
    """
    candidate = Path(__file__).resolve().parent.parent
    if (candidate / ".claude-plugin" / "marketplace.json").is_file():
        return candidate
    return None


def _get_installed_plugins_raw() -> object | None:
    """`claude plugin list --json` の生パース結果を返す。失敗時は None。"""
    result = _run_claude(["plugin", "list", "--json"])
    if result is None or result.returncode != 0:
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        logger.info(_log_format.format_status("plugins", "`claude plugin list --json` の出力 JSON を解析できませんでした"))
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
        logger.info(_log_format.format_status("plugins", f"marketplace.json の読み込みに失敗: {e}"))
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
            logger.info(_log_format.format_status(name, "project scope エントリに projectPath が無いためスキップ"))
            continue
        paths.append(Path(project_path))
    return paths


def _uninstall_deprecated(name: str, raw_data: object) -> bool:
    """Deprecated プラグインがインストール済みならアンインストールする。"""
    if not _is_installed(name, raw_data):
        return False
    result = _run_claude(["plugin", "uninstall", f"{name}@{_MARKETPLACE_NAME}"])
    if result is not None and result.returncode == 0:
        logger.info(_log_format.format_status(name, "deprecated のためアンインストールしました"))
        return True
    stderr = result.stderr.strip() if result else ""
    logger.info(_log_format.format_status(name, f"アンインストールに失敗: {stderr}"))
    return False


def _cleanup_old_project_scope(name: str, raw_data: object) -> None:
    """管理対象プラグインの project scope エントリを除去する (user scope 移行用)。

    `claude plugin uninstall --scope project` は呼び出し時の cwd から
    プロジェクト設定を特定するため、各エントリの projectPath を cwd に渡す。
    """
    for project_path in _project_scope_paths(name, raw_data):
        if not project_path.is_dir():
            logger.info(
                _log_format.format_status(
                    name,
                    f"project scope の除去対象 {project_path} が存在しないためスキップ "
                    "(必要に応じて ~/.claude/plugins/installed_plugins.json から手動で削除)",
                )
            )
            continue
        result = _run_claude(
            ["plugin", "uninstall", f"{name}@{_MARKETPLACE_NAME}", "--scope", "project"],
            cwd=project_path,
        )
        if result is not None and result.returncode == 0:
            logger.info(_log_format.format_status(name, f"project scope を除去しました ({project_path})"))
        else:
            stderr = result.stderr.strip() if result else ""
            logger.info(_log_format.format_status(name, f"project scope の除去に失敗 (続行): {stderr}"))


def _ensure_marketplace(dotfiles_root: Path) -> bool:
    """対象 marketplace を登録する (既に登録済みなら何もしない)。

    登録済みでもパスが相対パス化・別パスへ破損している場合は自動修復する。
    `update-dotfiles` を別環境で実行した際に `known_marketplaces.json` や
    `settings.json.extraKnownMarketplaces` に相対パスが残ると
    `Marketplace file not found` エラーになるため、両ファイルを点検する。
    """
    file_check = _check_marketplace_from_file(dotfiles_root)
    if file_check is True:
        return True
    if file_check is False:
        logger.info(_log_format.format_status("marketplace", "登録情報の不整合を検出。修復します"))
        return _repair_marketplace(dotfiles_root)

    # file_check is None: 両ファイルとも未登録。CLI経路で最終確認したうえで登録する。
    result = _run_claude(["plugin", "marketplace", "list", "--json"])
    if result is not None and result.returncode == 0:
        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError:
            data = None
        if _marketplace_already_registered(data):
            registered_path = _marketplace_registered_path(data)
            expected = str(dotfiles_root)
            if registered_path is None or registered_path == expected:
                return True
            logger.info(
                _log_format.format_status(
                    "marketplace",
                    f"パス不一致を検出 ({registered_path} != {expected})。再登録します",
                )
            )
            return _repair_marketplace(dotfiles_root)

    add_result = _run_claude(["plugin", "marketplace", "add", str(dotfiles_root)])
    if add_result is None or add_result.returncode != 0:
        stderr = add_result.stderr.strip() if add_result else ""
        logger.info(_log_format.format_status("marketplace", f"登録に失敗したためスキップ: {stderr}"))
        return False
    logger.info(_log_format.format_status("marketplace", f"{_MARKETPLACE_NAME} を登録しました"))
    return True


def _repair_marketplace(dotfiles_root: Path) -> bool:
    """壊れた marketplace 登録を段階的に修復する。

    1. ``claude plugin marketplace remove`` で既存エントリを除去 (失敗しても継続)
    2. ``claude plugin marketplace add <絶対パス>`` で再登録
    3. ``_check_marketplace_from_file`` で再検証し健全なら終了
    4. それでも解消しない場合は known_marketplaces.json と
       settings.json.extraKnownMarketplaces を直接書き換える (原子的置換)

    CLI の remove+add が settings.json 側を更新しないケースや、Claude Code 起動中の
    排他で書き込みが失敗するケースを JSON 直接書き換えで救済する。
    """
    expected = str(dotfiles_root)
    _run_claude(["plugin", "marketplace", "remove", _MARKETPLACE_NAME])
    add_result = _run_claude(["plugin", "marketplace", "add", expected])
    add_ok = add_result is not None and add_result.returncode == 0

    recheck = _check_marketplace_from_file(dotfiles_root)
    if recheck is True:
        logger.info(_log_format.format_status("marketplace", f"{_MARKETPLACE_NAME} を再登録しました"))
        return True
    # ファイル検査で判定不能 (両ファイル不在) でも CLI add が成功していれば登録済みとみなす
    if recheck is None and add_ok:
        logger.info(_log_format.format_status("marketplace", f"{_MARKETPLACE_NAME} を再登録しました"))
        return True

    logger.info(_log_format.format_status("marketplace", "CLI では修復できないため JSON を直接書き換えます"))
    known_ok = _rewrite_known_marketplaces_entry(dotfiles_root)
    extra_ok = _rewrite_settings_extra_known_entry(dotfiles_root)
    if known_ok and extra_ok:
        logger.info(_log_format.format_status("marketplace", "JSON 直接書き換えで修復しました"))
        return True
    logger.info(_log_format.format_status("marketplace", "修復に失敗しました"))
    return False


def _rewrite_known_marketplaces_entry(dotfiles_root: Path) -> bool:
    """known_marketplaces.json の対象エントリを directory 型の絶対パスで上書きする。

    他の marketplace キー (例: claude-plugins-official) は保持する。
    ファイル自体が無い場合は新規作成する (CLI add が失敗した直後のフォールバック用)。
    """
    path = _KNOWN_MARKETPLACES_PATH
    data = _load_json_dict(path)
    if data is None:
        return False
    data[_MARKETPLACE_NAME] = {
        "source": {"source": "directory", "path": str(dotfiles_root)},
        "installLocation": str(dotfiles_root),
    }
    return _atomic_write_json(path, data)


def _rewrite_settings_extra_known_entry(dotfiles_root: Path) -> bool:
    """settings.json の extraKnownMarketplaces[対象] を directory 型の絶対パスで上書きする。

    settings.json 自体が存在しない環境では書き込まない。
    known_marketplaces.json 側が健全ならそれだけで Claude Code は動作するため、
    ユーザーが作成していない settings.json を勝手に生成するのは避ける。
    """
    path = _SETTINGS_JSON_PATH
    if not path.exists():
        return True
    data = _load_json_dict(path)
    if data is None:
        return False
    extra = data.get("extraKnownMarketplaces")
    if not isinstance(extra, dict):
        extra = {}
        data["extraKnownMarketplaces"] = extra
    # settings.json の extraKnownMarketplaces は installLocation を持たない形式が正常
    cast("dict[str, object]", extra)[_MARKETPLACE_NAME] = {
        "source": {"source": "directory", "path": str(dotfiles_root)},
    }
    return _atomic_write_json(path, data)


def _load_json_dict(path: Path, *, silent: bool = False) -> dict[str, object] | None:
    """JSON ファイルをトップレベル dict として読み込む。

    ファイルが存在しない場合は空 dict を返し、新規作成の足場として使えるようにする。
    JSON 解析失敗・非 dict・I/O エラーは ``None`` を返し、呼び出し元で書き込みを中止させる。
    ``silent=True`` の場合、読み込み専用の検査用途として警告ログを抑制する。
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except OSError as e:
        if not silent:
            logger.info(_log_format.format_status("marketplace", f"{path.name} の読み込みに失敗: {e}"))
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        if not silent:
            logger.info(_log_format.format_status("marketplace", f"{path.name} の JSON 解析に失敗: {e}"))
        return None
    if not isinstance(data, dict):
        if not silent:
            logger.info(_log_format.format_status("marketplace", f"{path.name} がトップレベル dict でないためスキップ"))
        return None
    return cast("dict[str, object]", data)


def _atomic_write_json(path: Path, data: object) -> bool:
    """JSON ファイルを同一ディレクトリの tempfile + ``os.replace`` で原子的に書き出す。

    Claude Code 起動中の排他や他プロセスとの競合による書き込み失敗を拾い、
    ``False`` を返して呼び出し元に委ねる (post_apply 全体は落とさない)。
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        logger.info(_log_format.format_status("marketplace", f"ディレクトリ作成に失敗: {e}"))
        return False
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            delete=False,
            prefix=f"{path.name}.",
            suffix=".tmp",
        ) as tmp:
            json.dump(data, tmp, ensure_ascii=False, indent=2)
            tmp.write("\n")
            tmp_path = Path(tmp.name)
        os.replace(tmp_path, path)
        return True
    except OSError as e:
        logger.info(_log_format.format_status("marketplace", f"{path.name} の書き込みに失敗: {e}"))
        if tmp_path is not None:
            with contextlib.suppress(OSError):
                tmp_path.unlink()
        return False


def _marketplace_already_registered(data: object) -> bool:
    """対象 marketplace が既に登録されているかを判定する (`marketplace list` の出力をパース)。"""
    if isinstance(data, dict):
        dict_data = cast("dict[str, object]", data)
        if "marketplaces" in dict_data:
            return _marketplace_already_registered(dict_data["marketplaces"])
        return _MARKETPLACE_NAME in dict_data
    if isinstance(data, list):
        list_data = cast("list[object]", data)
        for item in list_data:
            if isinstance(item, dict) and cast("dict[str, object]", item).get("name") == _MARKETPLACE_NAME:
                return True
    return False


def _marketplace_registered_path(data: object) -> str | None:
    """登録済み marketplace のパスを抽出する。

    `known_marketplaces.json` に保存されるパスは `source.path` / `installLocation`
    / `path` のいずれかに格納される。取得できない場合は None を返し、呼び出し元で
    パス検証をスキップさせる (従来動作を維持)。
    """
    entry = _find_marketplace_entry(data)
    if entry is None:
        return None
    for key in ("path", "installLocation"):
        value = entry.get(key)
        if isinstance(value, str):
            return value
    # `source` が dict の場合はその中の `path` を見る（旧形式への後方互換）
    source = entry.get("source")
    if isinstance(source, dict):
        inner = cast("dict[str, object]", source).get("path")
        if isinstance(inner, str):
            return inner
    return None


def _find_marketplace_entry(data: object) -> dict[str, object] | None:
    """対象 marketplace のエントリ dict を返す。見つからなければ None。"""
    if isinstance(data, dict):
        dict_data = cast("dict[str, object]", data)
        if "marketplaces" in dict_data:
            return _find_marketplace_entry(dict_data["marketplaces"])
    if isinstance(data, list):
        for item in cast("list[object]", data):
            if isinstance(item, dict) and cast("dict[str, object]", item).get("name") == _MARKETPLACE_NAME:
                return cast("dict[str, object]", item)
    return None


def _install_plugin(name: str) -> bool:
    """指定 plugin をインストールする (成功時 True を返す)。"""
    result = _run_claude(["plugin", "install", f"{name}@{_MARKETPLACE_NAME}", "--scope", "user"])
    if result is None or result.returncode != 0:
        stderr = result.stderr.strip() if result else ""
        logger.info(_log_format.format_status(name, f"install に失敗: {stderr}"))
        return False
    logger.info(_log_format.format_status(name, "インストールしました"))
    return True


def _refresh_marketplace() -> bool:
    """Marketplace のメタデータを最新化する (`claude plugin marketplace update`)。

    ローカル marketplace.json の version 更新を取り込むために必要。
    失敗しても `plugin update` 側で回収できる可能性があるため best-effort 扱い。
    """
    result = _run_claude(["plugin", "marketplace", "update", _MARKETPLACE_NAME])
    if result is None or result.returncode != 0:
        stderr = result.stderr.strip() if result else ""
        logger.info(_log_format.format_status("marketplace", f"{_MARKETPLACE_NAME} の refresh に失敗 (続行): {stderr}"))
        return False
    return True


def _update_plugin(name: str) -> bool:
    """指定 plugin を最新版へ更新する (成功時 True を返す)。"""
    result = _run_claude(["plugin", "update", f"{name}@{_MARKETPLACE_NAME}", "--scope", "user"])
    if result is None or result.returncode != 0:
        stderr = result.stderr.strip() if result else ""
        logger.info(_log_format.format_status(name, f"update に失敗: {stderr}"))
        return False
    logger.info(_log_format.format_status(name, "更新しました"))
    return True


def _run_claude(args: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str] | None:
    """`claude` CLI を呼び出す共通ヘルパー。

    タイムアウト・例外・非ゼロ終了を全て吸収して呼び出し元に返す。
    `cwd` を指定すると project scope など cwd 依存のサブコマンドに対応できる。
    """
    try:
        return subprocess.run(
            ["claude", *args],
            capture_output=True,
            text=True,
            check=False,
            timeout=_CLAUDE_TIMEOUT,
            # Windows では text=True のデフォルトが cp932 になり、claude CLI の
            # UTF-8 日本語メッセージを読み取る reader thread で UnicodeDecodeError
            # が発生する。明示的に UTF-8 を指定し、不正なバイトが混入しても
            # 例外が発生しないよう errors="replace" を併用する。
            encoding="utf-8",
            errors="replace",
            cwd=cwd,
        )
    except (OSError, subprocess.SubprocessError) as e:
        logger.info(_log_format.format_status("claude", f"`{' '.join(args)}` 実行に失敗: {e}"))
        return None


if __name__ == "__main__":
    _main()

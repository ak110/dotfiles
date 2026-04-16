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

import json
import logging
import shutil
import subprocess
from pathlib import Path
from typing import cast

from pytools import _log_format

logger = logging.getLogger(__name__)

# marketplace.json の `name` と一致させる (.claude-plugin/marketplace.json を参照)
_MARKETPLACE_NAME = "ak110-dotfiles"

# `claude plugin` コマンドのタイムアウト (秒)
# ローカルパスからの install は通常 1-2 秒で終わるが、念のため余裕を持たせる
_CLAUDE_TIMEOUT = 30


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

    # 既にインストールされている対象 plugin が 1 つでもあるなら marketplace メタデータを
    # refresh して、ローカルの marketplace.json に入った version 更新を取り込む。
    # 新規 install しかない場合は install コマンドが毎回ファイルを読むため refresh 不要。
    if any(name in installed for name in target_versions):
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
        dict_data = cast("dict[object, object]", data)
        if "plugins" in dict_data:
            return _extract_plugin_version_map(dict_data["plugins"])
        return {key: "" for key in dict_data if isinstance(key, str)}
    if isinstance(data, list):
        list_data = cast("list[object]", data)
        versions: dict[str, str] = {}
        for item in list_data:
            if isinstance(item, dict):
                item_dict = cast("dict[object, object]", item)
                # user scope 以外のエントリは管理対象外
                if item_dict.get("scope") not in (None, "user"):
                    continue
                name = _name_from_entry(item_dict)
                if name is not None:
                    raw_version = item_dict.get("version")
                    versions[name] = raw_version if isinstance(raw_version, str) else ""
        return versions
    return {}


def _name_from_entry(entry: dict[object, object]) -> str | None:
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
        if isinstance(item, dict) and _name_from_entry(cast("dict[object, object]", item)) == name:
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
        entry = cast("dict[object, object]", item)
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

    登録済みでもパスが不一致の場合は再登録する。`update-dotfiles` を別環境で
    実行した際に `known_marketplaces.json` に相対パスが残ると `Marketplace file
    not found` エラーになるため、パスを検証して自動修復する。
    """
    result = _run_claude(["plugin", "marketplace", "list", "--json"])
    if result is not None and result.returncode == 0:
        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError:
            data = None
        if _marketplace_already_registered(data):
            registered_path = _marketplace_registered_path(data)
            expected = str(dotfiles_root)
            if registered_path is not None and registered_path != expected:
                # パス不一致 → remove + add で再登録
                logger.info(
                    _log_format.format_status(
                        "marketplace",
                        f"パス不一致を検出 ({registered_path} != {expected})。再登録します",
                    )
                )
                _run_claude(["plugin", "marketplace", "remove", _MARKETPLACE_NAME])
            else:
                return True

    # 未登録 (またはパス不一致で remove 済み) なら add する
    add_result = _run_claude(["plugin", "marketplace", "add", str(dotfiles_root)])
    if add_result is None or add_result.returncode != 0:
        stderr = add_result.stderr.strip() if add_result else ""
        logger.info(_log_format.format_status("marketplace", f"登録に失敗したためスキップ: {stderr}"))
        return False
    logger.info(_log_format.format_status("marketplace", f"{_MARKETPLACE_NAME} を登録しました"))
    return True


def _marketplace_already_registered(data: object) -> bool:
    """対象 marketplace が既に登録されているかを判定する (`marketplace list` の出力をパース)。"""
    if isinstance(data, dict):
        dict_data = cast("dict[object, object]", data)
        if "marketplaces" in dict_data:
            return _marketplace_already_registered(dict_data["marketplaces"])
        return _MARKETPLACE_NAME in dict_data
    if isinstance(data, list):
        list_data = cast("list[object]", data)
        for item in list_data:
            if isinstance(item, dict) and cast("dict[object, object]", item).get("name") == _MARKETPLACE_NAME:
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
        inner = cast("dict[object, object]", source).get("path")
        if isinstance(inner, str):
            return inner
    return None


def _find_marketplace_entry(data: object) -> dict[object, object] | None:
    """対象 marketplace のエントリ dict を返す。見つからなければ None。"""
    if isinstance(data, dict):
        dict_data = cast("dict[object, object]", data)
        if "marketplaces" in dict_data:
            return _find_marketplace_entry(dict_data["marketplaces"])
    if isinstance(data, list):
        for item in cast("list[object]", data):
            if isinstance(item, dict) and cast("dict[object, object]", item).get("name") == _MARKETPLACE_NAME:
                return cast("dict[object, object]", item)
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

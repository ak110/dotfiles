"""dotfiles 同梱の Claude Code plugin を自動インストール/更新する。

本モジュールは `chezmoi apply` 後処理 (`pytools.post_apply`) から呼ばれる。
必要な前提条件をすべて満たす場合だけ動作し、満たさない場合は
完全にスキップする (dotfiles apply 全体を落とさないための安全側動作)。

前提条件 (すべて必要):

1. `claude` CLI が PATH にある
2. `uv` CLI が PATH にある (plugin の hook スクリプトが `uv run --script` で動くため)

前提を満たした場合の処理:

- marketplace 未登録 → `claude plugin marketplace add <dotfiles root>` で登録
  (ローカルパスを使うのは、オフライン環境でも動くため。GitHub 経由では
   ネットワーク依存になる)
- 対象 plugin が未インストール → `claude plugin install <name>@<marketplace> --scope user`
- 対象 plugin がインストール済みで `marketplace.json` と version が乖離
  → `claude plugin marketplace update <name>` で marketplace メタデータを更新した後、
     `claude plugin update <name>@<marketplace>` で反映
  (version が一致していれば update コマンドは呼ばずスキップ)

`update-dotfiles` 経由で本モジュールが実行されるたびに version 乖離が解消されるため、
ユーザー環境では marketplace.json の bump が自動で反映される。
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

# インストール対象 plugin 名 (plugins/<name>/.claude-plugin/plugin.json を参照)
_PLUGIN_NAMES = ("edit-guardrails",)

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

    installed = _list_installed_plugin_versions()
    if installed is None:
        logger.info(_log_format.format_status("plugins", "インストール済み plugin 一覧の取得に失敗したためスキップ"))
        return False

    if not _ensure_marketplace(dotfiles_root):
        return False

    target_versions = _read_target_versions(dotfiles_root)

    # 既にインストールされている plugin が 1 つでもあるなら marketplace メタデータを
    # refresh して、ローカルの marketplace.json に入った version bump を取り込む。
    # 新規 install しかない場合は install コマンドが毎回ファイルを読むため refresh 不要。
    if any(name in installed for name in _PLUGIN_NAMES):
        _refresh_marketplace()

    any_change = False
    for name in _PLUGIN_NAMES:
        current = installed.get(name)
        target = target_versions.get(name)
        if current is None:
            if _install_plugin(name):
                any_change = True
        elif target is not None and current != target:
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


def _list_installed_plugin_versions() -> dict[str, str] | None:
    """インストール済み plugin 名 → version の辞書を返す。失敗時は None。

    version 不明な要素は空文字列で登録する (呼び出し側で差分判定から除外される)。
    """
    result = _run_claude(["plugin", "list", "--json"])
    if result is None or result.returncode != 0:
        return None
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        logger.info(_log_format.format_status("plugins", "`claude plugin list --json` の出力 JSON を解析できませんでした"))
        return None
    # Claude Code の出力形式は将来変わる可能性があるため複数形式を許容する
    return _extract_plugin_version_map(data)


def _extract_plugin_version_map(data: object) -> dict[str, str]:
    """`claude plugin list --json` の戻り値から name → version の辞書を作る。

    実機で確認した形式 (Claude Code 2.x): list[dict] で各要素が以下を持つ。

    - `id`: `<plugin>@<marketplace>` 形式 (例: `edit-guardrails@ak110-dotfiles`)
    - `version`: 例 `"0.1.0"` (無い場合は空文字列として記録)
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


def _read_target_versions(dotfiles_root: Path) -> dict[str, str]:
    """`marketplace.json` から配布側の name → version 辞書を作る。

    読み込み失敗時は空辞書を返す (更新判定をスキップする方針とする)。
    """
    manifest = dotfiles_root / ".claude-plugin" / "marketplace.json"
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        logger.info(_log_format.format_status("plugins", f"marketplace.json の読み込みに失敗: {e}"))
        return {}
    plugins = data.get("plugins") if isinstance(data, dict) else None
    if not isinstance(plugins, list):
        return {}
    versions: dict[str, str] = {}
    for entry in plugins:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        version = entry.get("version")
        if isinstance(name, str) and isinstance(version, str):
            versions[name] = version
    return versions


def _ensure_marketplace(dotfiles_root: Path) -> bool:
    """対象 marketplace を登録する (既に登録済みなら何もしない)。"""
    # 既に登録済みかチェック
    result = _run_claude(["plugin", "marketplace", "list", "--json"])
    if result is not None and result.returncode == 0:
        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError:
            data = None
        if _marketplace_already_registered(data):
            return True

    # 未登録なら add する
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

    ローカル marketplace.json の version bump を取り込むために必要。
    失敗しても `plugin update` 側で拾える可能性があるため best-effort 扱い。
    """
    result = _run_claude(["plugin", "marketplace", "update", _MARKETPLACE_NAME])
    if result is None or result.returncode != 0:
        stderr = result.stderr.strip() if result else ""
        logger.info(_log_format.format_status("marketplace", f"{_MARKETPLACE_NAME} の refresh に失敗 (続行): {stderr}"))
        return False
    return True


def _update_plugin(name: str) -> bool:
    """指定 plugin を最新版へ更新する (成功時 True を返す)。"""
    result = _run_claude(["plugin", "update", f"{name}@{_MARKETPLACE_NAME}"])
    if result is None or result.returncode != 0:
        stderr = result.stderr.strip() if result else ""
        logger.info(_log_format.format_status(name, f"update に失敗: {stderr}"))
        return False
    logger.info(_log_format.format_status(name, "更新しました"))
    return True


def _run_claude(args: list[str]) -> subprocess.CompletedProcess[str] | None:
    """`claude` CLI を呼び出す共通ヘルパー。

    タイムアウト・例外・非ゼロ終了を全て吸収して呼び出し元に返す。
    """
    try:
        return subprocess.run(
            ["claude", *args],
            capture_output=True,
            text=True,
            check=False,
            timeout=_CLAUDE_TIMEOUT,
            # Windows では text=True のデフォルトが cp932 になり、claude CLI の
            # UTF-8 日本語メッセージを読み取る reader thread が UnicodeDecodeError
            # を出す。明示的に UTF-8 を指定し、万一不正バイトがあっても落ちない
            # よう errors="replace" で保険をかける。
            encoding="utf-8",
            errors="replace",
        )
    except (OSError, subprocess.SubprocessError) as e:
        logger.info(_log_format.format_status("claude", f"`{' '.join(args)}` 実行に失敗: {e}"))
        return None


if __name__ == "__main__":
    _main()

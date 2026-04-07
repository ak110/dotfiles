"""dotfiles 同梱の Claude Code plugin を自動インストールする。

本モジュールは `chezmoi apply` 後処理 (`pytools.post_apply`) から呼ばれる。
必要な前提条件をすべて満たす場合だけ plugin を入れ、満たさない場合は
完全にスキップする (dotfiles apply 全体を落とさないための安全側動作)。

前提条件 (すべて必要):

1. `claude` CLI が PATH にある
2. `uv` CLI が PATH にある (plugin の hook スクリプトが `uv run --script` で動くため)
3. 対象 plugin がまだインストールされていない

条件を満たした場合は以下を実行する:

1. `claude plugin marketplace add <dotfiles root>` で marketplace を登録
   (ローカルパスを使うのは、オフライン環境でも動くため。GitHub 経由では
    ネットワーク依存になる)
2. `claude plugin install <name>@<marketplace> --scope user` で plugin を導入

既にインストール済みの場合はノータッチ (更新は `claude plugin update` を
ユーザー自身が打つか、将来的に別ステップとして実装する)。
"""

import json
import logging
import shutil
import subprocess
from pathlib import Path
from typing import cast

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
    """Claude Code plugin を必要ならインストールする。

    Returns:
        何らかの plugin を新たにインストールしたら True。
    """
    if not _prerequisites_ok():
        return False

    dotfiles_root = _find_dotfiles_root()
    if dotfiles_root is None:
        logger.info("  -> dotfiles ルート (marketplace.json のあるディレクトリ) が見つからずスキップ")
        return False

    installed = _list_installed_plugins()
    if installed is None:
        logger.info("  -> インストール済み plugin 一覧の取得に失敗したためスキップ")
        return False

    missing = [name for name in _PLUGIN_NAMES if name not in installed]
    if not missing:
        logger.info("  -> 全 plugin インストール済み")
        return False

    if not _ensure_marketplace(dotfiles_root):
        return False

    any_installed = False
    for name in missing:
        if _install_plugin(name):
            any_installed = True
    return any_installed


def _prerequisites_ok() -> bool:
    """前提条件 (claude と uv の両方が PATH にあるか) を確認する。"""
    if shutil.which("claude") is None:
        logger.info("  -> claude CLI 未検出のためスキップ")
        return False
    if shutil.which("uv") is None:
        logger.info("  -> uv CLI 未検出のためスキップ (plugin hook は uv run --script を使う)")
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


def _list_installed_plugins() -> set[str] | None:
    """インストール済み plugin 名の集合を返す。失敗時は None。"""
    result = _run_claude(["plugin", "list", "--json"])
    if result is None or result.returncode != 0:
        return None
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        logger.info("  -> `claude plugin list --json` の出力 JSON を解析できませんでした")
        return None
    # Claude Code の出力形式は将来変わる可能性があるため複数形式を許容する
    return set(_extract_plugin_names(data))


def _extract_plugin_names(data: object) -> list[str]:
    """`claude plugin list --json` の戻り値から plugin 名を抽出する。

    実機で確認した形式 (Claude Code 2.x): list[dict] で各要素が以下を持つ。

    - `id`: `<plugin>@<marketplace>` 形式 (例: `edit-guardrails@ak110-dotfiles`)
    - `name`: ない場合あり

    知っている全形式:

    - list[dict]: 各要素の `id` (`@` の前を切り出す) または `name` フィールド
    - dict[str, ...]: キーが plugin 名
    - dict に `plugins` キーがあり、その中に上記いずれか

    未知の形式では空リストを返す (呼び出し側でスキップ扱いになる)。
    """
    if isinstance(data, dict):
        dict_data = cast("dict[object, object]", data)
        if "plugins" in dict_data:
            return _extract_plugin_names(dict_data["plugins"])
        return [key for key in dict_data if isinstance(key, str)]
    if isinstance(data, list):
        list_data = cast("list[object]", data)
        names: list[str] = []
        for item in list_data:
            if isinstance(item, dict):
                item_dict = cast("dict[object, object]", item)
                name = _name_from_entry(item_dict)
                if name is not None:
                    names.append(name)
        return names
    return []


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
        logger.info("  -> marketplace 登録に失敗したためスキップ: %s", stderr)
        return False
    logger.info("  -> marketplace %s を登録しました", _MARKETPLACE_NAME)
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
        logger.info("  -> plugin %s の install に失敗: %s", name, stderr)
        return False
    logger.info("  -> plugin %s をインストールしました", name)
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
        )
    except (OSError, subprocess.SubprocessError) as e:
        logger.info("  -> `claude %s` 実行に失敗: %s", " ".join(args), e)
        return None


if __name__ == "__main__":
    _main()

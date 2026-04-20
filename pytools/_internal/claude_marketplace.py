"""install_claude_plugins が担う chezmoi apply 後処理から呼ばれる、marketplace 登録・検証・修復専用モジュール。

本モジュールは marketplace をローカルの dotfiles リポジトリを参照する directory 型で登録する。
GitHub 型は install-claude.sh/`.ps1` の bootstrap 経路が残している旧形式であり、
chezmoi apply 経由で呼ばれた際に directory 型へ自動マイグレーションする。

directory 型を使う理由は、dotfiles で編集した内容が push/update サイクルを介さずに
ユーザー環境へ反映されること。キャッシュ同期は `install_claude_plugins` 側で
`plugin install` の再実行として行う。

公開 API:
- `ensure_marketplace()`: marketplace を directory 型で登録する (未登録・旧形式は自動マイグレーション)
- `repair_marketplace()`: 壊れた・旧形式の marketplace 登録を段階的に修復する
- `is_directory_type_registered()`: 現在の登録が directory 型で健全かを返す
"""

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from pytools._internal import claude_common, log_format

logger = logging.getLogger(__name__)

# --- marketplace 専用定数 ---

# marketplace 登録情報は known_marketplaces.json と settings.json.extraKnownMarketplaces の
# 2 箇所に保存されるため、両方を点検・修復する必要がある。
_KNOWN_MARKETPLACES_PATH = Path.home() / ".claude" / "plugins" / "known_marketplaces.json"

# patch 対象として露出させるためモジュールローカル変数として保持する。
_SETTINGS_JSON_PATH = claude_common.SETTINGS_JSON_PATH


# --- dotfiles ルート検出 ---


def _find_dotfiles_root() -> Path | None:
    """本ファイルから見た dotfiles ルートディレクトリを返す。

    dotfiles ルートは `.claude-plugin/marketplace.json` を持つ。
    `pytools/_internal/claude_marketplace.py` は dotfiles/pytools/_internal/ に置かれているため
    親の親の親がルート。

    install_claude_plugins._find_dotfiles_root() と同じ実装だが、循環 import を避けるため
    本モジュールでも独立に検出する (marker ファイル判定で済むシンプルな処理のため)。
    """
    candidate = Path(__file__).resolve().parent.parent.parent
    if (candidate / ".claude-plugin" / "marketplace.json").is_file():
        return candidate
    return None


# --- 内部ヘルパー (テスト・デバッグ用に公開) ---


def _check_marketplace_from_file() -> bool | None:
    """known_marketplaces.json と settings.json.extraKnownMarketplaces の両方を検査する。

    Returns:
        True: どちらも directory 型 + dotfiles 絶対パスで健全。
        False: どちらかが壊れている (旧 GitHub 型・別 path・`source` 欠落など)。
        None: 両ファイルとも未登録。CLI 経路で新規登録が必要。

    2 ファイルを両方検査する理由:
        CLI の ``claude plugin marketplace remove``/``add`` が両ファイルを同時に
        更新しないケースがあり、片方だけに過去の登録が残留して
        ``Marketplace file not found`` エラーを引き起こす環境が確認されている。
        どちらか片方でも壊れていれば修復対象とする。
    """
    known = _load_known_marketplace_entry()
    extra = _load_extra_known_marketplace_entry()
    if known is None and extra is None:
        return None
    if known is not None and not _is_entry_healthy(known):
        return False
    return not (extra is not None and not _is_entry_healthy(extra))


def _load_known_marketplace_entry() -> dict[str, object] | None:
    """known_marketplaces.json から対象 marketplace のエントリを読み込む。"""
    data = claude_common.load_json_dict(_KNOWN_MARKETPLACES_PATH, silent=True)
    if data is None:
        return None
    entry = data.get(claude_common.MARKETPLACE_NAME)
    return cast("dict[str, object]", entry) if isinstance(entry, dict) else None


def _load_extra_known_marketplace_entry() -> dict[str, object] | None:
    """settings.json.extraKnownMarketplaces から対象 marketplace のエントリを読み込む。"""
    data = claude_common.load_json_dict(_SETTINGS_JSON_PATH, silent=True)
    if data is None:
        return None
    extra = data.get("extraKnownMarketplaces")
    if not isinstance(extra, dict):
        return None
    entry = cast("dict[str, object]", extra).get(claude_common.MARKETPLACE_NAME)
    return cast("dict[str, object]", entry) if isinstance(entry, dict) else None


def _is_entry_healthy(entry: dict[str, object]) -> bool:
    """登録エントリが directory 型の正常形式かを判定する。

    正常形式は ``source = {source: "directory", path: <dotfiles 絶対パス>}``。
    旧 GitHub 型 (``{"source": "github", "repo": "ak110/dotfiles"}``) は
    「旧形式」として False と判定し、自動マイグレーションの対象とする。
    別 path・``source`` 欠落・非 dict もすべて False。

    dotfiles ルートを検出できない環境 (チェックアウトが壊れているなど) では
    比較対象が無いため False を返し、CLI 経路で再登録させる。
    """
    source = entry.get("source")
    if not isinstance(source, dict):
        return False
    source_dict = cast("dict[str, object]", source)
    if source_dict.get("source") != "directory":
        return False
    dotfiles_root = _find_dotfiles_root()
    if dotfiles_root is None:
        return False
    return source_dict.get("path") == str(dotfiles_root)


def is_directory_type_registered() -> bool:
    """現在の登録が directory 型で健全かを返す。

    ``install_claude_plugins`` から「version 乖離に依存せず毎回 `plugin install`
    を再実行するか」を判断するために参照する。健全な directory 型登録があれば
    True、旧 GitHub 型や未登録の場合は False。

    判定は `_check_marketplace_from_file()` と同じロジックを使い、
    ``True`` のみを directory 型健全とみなす。
    """
    return _check_marketplace_from_file() is True


def _marketplace_already_registered(data: object) -> bool:
    """対象 marketplace が既に登録されているかを判定する (`marketplace list` の出力をパース)。"""
    if isinstance(data, dict):
        dict_data = cast("dict[str, object]", data)
        if "marketplaces" in dict_data:
            return _marketplace_already_registered(dict_data["marketplaces"])
        return claude_common.MARKETPLACE_NAME in dict_data
    if isinstance(data, list):
        list_data = cast("list[object]", data)
        for item in list_data:
            if isinstance(item, dict) and cast("dict[str, object]", item).get("name") == claude_common.MARKETPLACE_NAME:
                return True
    return False


def refresh_marketplace() -> bool:
    """Marketplace のメタデータを最新化する (`claude plugin marketplace update`)。

    JSON 直接書き換え後に書き換え内容が有効かを確認するために呼ぶ。
    壊れたエントリが残っていれば CLI 側でエラーが出るため早期検知できる。
    directory 型ではキャッシュ同期には効かない (validation のみ) ため、
    通常フローのキャッシュ同期は `install_claude_plugins._install_plugin` で行う。
    失敗しても best-effort 扱いで続行する。
    """
    result = claude_common.run_claude(["plugin", "marketplace", "update", claude_common.MARKETPLACE_NAME])
    if result is None or result.returncode != 0:
        logger.info(
            log_format.format_status(
                "marketplace",
                f"{claude_common.MARKETPLACE_NAME} の refresh に失敗 (続行): {claude_common.format_cli_error(result)}",
            )
        )
        return False
    return True


def _now_iso_millis() -> str:
    """現在時刻を ISO 8601 (UTC, ミリ秒精度, 末尾 Z) で返す。"""
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


# --- 公開 API ---


def ensure_marketplace() -> bool:
    """対象 marketplace を directory 型で登録する (既に directory 型で健全なら何もしない)。

    登録済みでも旧 GitHub 型・別 path などで破損している場合は自動でマイグレーションする。
    install-claude.sh/`.ps1` の bootstrap で GitHub 型が登録された状態から chezmoi apply
    経由で呼ばれることを想定している。
    """
    file_check = _check_marketplace_from_file()
    if file_check is True:
        return True
    if file_check is False:
        logger.info(log_format.format_status("marketplace", "登録情報の不整合を検出。修復します"))
        return repair_marketplace()

    # file_check is None: 両ファイルとも未登録。CLI 経路で最終確認したうえで登録する。
    result = claude_common.run_claude(["plugin", "marketplace", "list", "--json"])
    if result is not None and result.returncode == 0:
        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError:
            data = None
        if _marketplace_already_registered(data):
            return True

    dotfiles_root = _find_dotfiles_root()
    if dotfiles_root is None:
        logger.info(log_format.format_status("marketplace", "dotfiles ルートが見つからず登録をスキップ"))
        return False
    add_result = claude_common.run_claude(
        ["plugin", "marketplace", "add", str(dotfiles_root), "--scope", "user"],
    )
    if add_result is None or add_result.returncode != 0:
        stderr = add_result.stderr.strip() if add_result else ""
        logger.info(log_format.format_status("marketplace", f"登録に失敗したためスキップ: {stderr}"))
        return False
    logger.info(log_format.format_status("marketplace", f"{claude_common.MARKETPLACE_NAME} を登録しました"))
    return True


def repair_marketplace() -> bool:
    """壊れた marketplace 登録を段階的に修復する。

    1. ``claude plugin marketplace remove`` で既存エントリを除去 (失敗しても継続)
    2. ``claude plugin marketplace add <dotfiles 絶対パス> --scope user`` で directory 型として再登録
    3. ``_check_marketplace_from_file`` で再検証し健全なら終了
    4. それでも解消しない場合は known_marketplaces.json と
       settings.json.extraKnownMarketplaces を directory 型エントリで直接書き換え、
       続けて ``claude plugin marketplace update`` を呼んで整合性を確認する

    CLI の remove+add が settings.json 側を更新しないケースや、Claude Code 起動中の
    排他で書き込みが失敗するケースを JSON 直接書き換えで救済する。

    Note:
        JSON 直接書き換えのフォールバック経路は Claude Code CLI の既知不具合
        (``marketplace add`` 直後も ``settings.json.extraKnownMarketplaces`` を
         更新しない環境が存在する) への回避策であり、内部ファイル形式に依存する。
        CLI 側で該当不具合が解消したら、直接書き換え経路とその関連ヘルパー
        (``_rewrite_known_marketplaces_entry`` / ``_rewrite_settings_extra_known_entry``
         / ``_now_iso_millis`` / ``claude_common.atomic_write_json``) は削除候補となる。
    """
    dotfiles_root = _find_dotfiles_root()
    if dotfiles_root is None:
        logger.info(log_format.format_status("marketplace", "dotfiles ルートが見つからず修復をスキップ"))
        return False

    claude_common.run_claude(["plugin", "marketplace", "remove", claude_common.MARKETPLACE_NAME])
    add_result = claude_common.run_claude(
        ["plugin", "marketplace", "add", str(dotfiles_root), "--scope", "user"],
    )
    add_ok = add_result is not None and add_result.returncode == 0

    recheck = _check_marketplace_from_file()
    if recheck is True:
        logger.info(log_format.format_status("marketplace", f"{claude_common.MARKETPLACE_NAME} を再登録しました"))
        return True
    # ファイル検査で判定不能 (両ファイル不在) でも CLI add が成功していれば登録済みとみなす
    if recheck is None and add_ok:
        logger.info(log_format.format_status("marketplace", f"{claude_common.MARKETPLACE_NAME} を再登録しました"))
        return True

    logger.info(log_format.format_status("marketplace", "CLI では修復できないため JSON を直接書き換えます"))
    known_ok = _rewrite_known_marketplaces_entry(dotfiles_root)
    extra_ok = _rewrite_settings_extra_known_entry(dotfiles_root)
    if known_ok and extra_ok:
        # 書き換え後のメタデータ整合を念のため確認する (directory 型では validation のみだが、
        # 壊れたエントリが残っていれば CLI 側でエラーが出るため早期検知できる)
        refresh_marketplace()
        logger.info(log_format.format_status("marketplace", "JSON 直接書き換えで修復しました"))
        return True
    logger.info(log_format.format_status("marketplace", "修復に失敗しました"))
    return False


def _rewrite_known_marketplaces_entry(dotfiles_root: Path) -> bool:
    """known_marketplaces.json の対象エントリを directory 型で上書きする。

    他の marketplace キー (例: claude-plugins-official) は保持する。
    ファイル自体が無い場合は新規作成する (CLI add が失敗した直後のフォールバック用)。
    ``lastUpdated`` を欠落させると後続の ``marketplace update`` が
    ``Invalid input: expected string, received undefined`` で失敗するため、
    現在時刻を ISO 8601 文字列として書き込む。
    ``installLocation`` は directory 型でも ``source.path`` と同一のフルパスを書く
    (`claude plugin marketplace add` で登録された正常エントリ形式に合わせる)。
    """
    path = _KNOWN_MARKETPLACES_PATH
    data = claude_common.load_json_dict(path, tag="marketplace")
    if data is None:
        return False
    data[claude_common.MARKETPLACE_NAME] = {
        "source": {"source": "directory", "path": str(dotfiles_root)},
        "installLocation": str(dotfiles_root),
        "lastUpdated": _now_iso_millis(),
    }
    return claude_common.atomic_write_json(path, data, tag="marketplace")


def _rewrite_settings_extra_known_entry(dotfiles_root: Path) -> bool:
    """settings.json の extraKnownMarketplaces[対象] を directory 型で上書きする。

    settings.json 自体が存在しない環境では書き込まない。
    known_marketplaces.json 側が健全ならそれだけで Claude Code は動作するため、
    ユーザーが作成していない settings.json を勝手に生成するのは避ける。
    正常登録形式 (claude-plugins-official と同様) に揃え ``installLocation`` は持たせない。
    """
    path = _SETTINGS_JSON_PATH
    if not path.exists():
        return True
    data = claude_common.load_json_dict(path, tag="marketplace")
    if data is None:
        return False
    extra = data.get("extraKnownMarketplaces")
    if not isinstance(extra, dict):
        extra = {}
        data["extraKnownMarketplaces"] = extra
    cast("dict[str, object]", extra)[claude_common.MARKETPLACE_NAME] = {
        "source": {"source": "directory", "path": str(dotfiles_root)},
    }
    return claude_common.atomic_write_json(path, data, tag="marketplace")

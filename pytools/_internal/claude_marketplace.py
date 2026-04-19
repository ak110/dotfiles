"""install_claude_plugins が担う chezmoi apply 後処理から呼ばれる、marketplace 登録・検証・修復専用モジュール。

公開 API:
- `ensure_marketplace()`: marketplace を GitHub 型で登録する (既登録・破損時の修復を含む)
- `repair_marketplace()`: 破損した marketplace 登録を段階的に修復する
"""

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from pytools._internal import claude_common, log_format

logger = logging.getLogger(__name__)

# --- marketplace 専用定数 ---

# marketplace 登録時の `source.repo` 値。GitHub ショートハンドとしても使う。
_MARKETPLACE_REPO = "ak110/dotfiles"

# GitHub 型登録時の installLocation。Claude Code が慣例的に
# `~/.claude/plugins/marketplaces/<name>/` 配下へ clone するため、これに合わせる。
_MARKETPLACE_INSTALL_LOCATION = Path.home() / ".claude" / "plugins" / "marketplaces" / claude_common.MARKETPLACE_NAME

# marketplace 登録情報は known_marketplaces.json と settings.json.extraKnownMarketplaces の
# 2 箇所に保存されるため、両方を点検・修復する必要がある。
_KNOWN_MARKETPLACES_PATH = Path.home() / ".claude" / "plugins" / "known_marketplaces.json"

# patch 対象として露出させるためモジュールローカル変数として保持する。
_SETTINGS_JSON_PATH = claude_common.SETTINGS_JSON_PATH


# --- 内部ヘルパー (テスト・デバッグ用に公開) ---


def _check_marketplace_from_file() -> bool | None:
    """known_marketplaces.json と settings.json.extraKnownMarketplaces の両方を検査する。

    Returns:
        True: どちらも（存在する側が）``source = {source: "github", repo: _MARKETPLACE_REPO}``。
        False: どちらかが壊れている（directory 型・別 repo・欠落など）。
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
    """登録エントリが GitHub 型の正常形式かを判定する。

    正常形式は ``source = {source: "github", repo: _MARKETPLACE_REPO}``。
    directory 型・別 repo・``source`` 欠落・非 dict はすべて False。
    過去の update-dotfiles で残った directory 型エントリはこれにより壊れたエントリと
    判定され、自動マイグレーションの対象となる。
    """
    source = entry.get("source")
    if not isinstance(source, dict):
        return False
    source_dict = cast("dict[str, object]", source)
    return source_dict.get("source") == "github" and source_dict.get("repo") == _MARKETPLACE_REPO


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

    ローカル marketplace.json の version 更新を取り込むために必要。
    失敗しても `plugin update` 側で回収できる可能性があるため best-effort 扱い。
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
    """対象 marketplace を GitHub 型で登録する (既に登録済みなら何もしない)。

    登録済みでも directory 型・別 repo などで破損している場合は自動でマイグレーションする。
    `update-dotfiles` を別環境で実行した際に `known_marketplaces.json` や
    `settings.json.extraKnownMarketplaces` に過去の directory 型エントリが残ると
    `Marketplace file not found` エラーになるため、両ファイルを点検する。
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

    add_result = claude_common.run_claude(["plugin", "marketplace", "add", _MARKETPLACE_REPO])
    if add_result is None or add_result.returncode != 0:
        stderr = add_result.stderr.strip() if add_result else ""
        logger.info(log_format.format_status("marketplace", f"登録に失敗したためスキップ: {stderr}"))
        return False
    logger.info(log_format.format_status("marketplace", f"{claude_common.MARKETPLACE_NAME} を登録しました"))
    return True


def repair_marketplace() -> bool:
    """壊れた marketplace 登録を段階的に修復する。

    1. ``claude plugin marketplace remove`` で既存エントリを除去 (失敗しても継続)
    2. ``claude plugin marketplace add ak110/dotfiles`` で GitHub 型として再登録
    3. ``_check_marketplace_from_file`` で再検証し健全なら終了
    4. それでも解消しない場合は known_marketplaces.json と
       settings.json.extraKnownMarketplaces を GitHub 型エントリで直接書き換え、
       続けて ``claude plugin marketplace update`` を呼び git clone を誘発する

    CLI の remove+add が settings.json 側を更新しないケースや、Claude Code 起動中の
    排他で書き込みが失敗するケースを JSON 直接書き換えで救済する。直接書き換えは
    ``installLocation`` のディレクトリ実体を作らないため、後続の ``marketplace update``
    でリポジトリを clone させる必要がある。

    Note:
        JSON 直接書き換えのフォールバック経路は Claude Code CLI の既知不具合
        (``marketplace add`` 直後も ``settings.json.extraKnownMarketplaces`` を
         更新しない環境が存在する) への回避策であり、内部ファイル形式に依存する。
        CLI 側で該当不具合が解消したら、直接書き換え経路とその関連ヘルパー
        (``_rewrite_known_marketplaces_entry`` / ``_rewrite_settings_extra_known_entry``
         / ``_now_iso_millis`` / ``claude_common.atomic_write_json``) は削除候補となる。
    """
    claude_common.run_claude(["plugin", "marketplace", "remove", claude_common.MARKETPLACE_NAME])
    add_result = claude_common.run_claude(["plugin", "marketplace", "add", _MARKETPLACE_REPO])
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
    known_ok = _rewrite_known_marketplaces_entry()
    extra_ok = _rewrite_settings_extra_known_entry()
    if known_ok and extra_ok:
        # installLocation のディレクトリ実体が無い状態で終わらせないよう git clone を誘発する
        refresh_marketplace()
        logger.info(log_format.format_status("marketplace", "JSON 直接書き換えで修復しました"))
        return True
    logger.info(log_format.format_status("marketplace", "修復に失敗しました"))
    return False


def _rewrite_known_marketplaces_entry() -> bool:
    """known_marketplaces.json の対象エントリを GitHub 型で上書きする。

    他の marketplace キー (例: claude-plugins-official) は保持する。
    ファイル自体が無い場合は新規作成する (CLI add が失敗した直後のフォールバック用)。
    ``lastUpdated`` を欠落させると後続の ``marketplace update`` が
    ``Invalid input: expected string, received undefined`` で失敗するため、
    現在時刻を ISO 8601 文字列として書き込む。
    """
    path = _KNOWN_MARKETPLACES_PATH
    data = claude_common.load_json_dict(path, tag="marketplace")
    if data is None:
        return False
    data[claude_common.MARKETPLACE_NAME] = {
        "source": {"source": "github", "repo": _MARKETPLACE_REPO},
        "installLocation": str(_MARKETPLACE_INSTALL_LOCATION),
        "lastUpdated": _now_iso_millis(),
    }
    return claude_common.atomic_write_json(path, data, tag="marketplace")


def _rewrite_settings_extra_known_entry() -> bool:
    """settings.json の extraKnownMarketplaces[対象] を GitHub 型で上書きする。

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
        "source": {"source": "github", "repo": _MARKETPLACE_REPO},
    }
    return claude_common.atomic_write_json(path, data, tag="marketplace")

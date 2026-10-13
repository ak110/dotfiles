"""Gemini CLIの管理設定をJSONCのユーザー設定へ反映する。"""

__lazy_modules__ = {"pytilpack.jsonc"}

import copy
import json
import logging
from pathlib import Path

import pytilpack.jsonc

from pytools._internal import common, log_format, post_apply_outcome

logger = logging.getLogger(__name__)


def run(*, home: Path | None = None, dotfiles_root: Path | None = None) -> post_apply_outcome.PostApplyOutcome:
    """両OSでホーム配下のGemini設定を更新し、変更有無または失敗と復旧操作を返す。

    Args:
        home: 配布先のホーム。省略時はPath.home()を使う。
        dotfiles_root: 配布原本を持つ作業ツリー。省略時は共通の解決処理を使う。
    """
    root = dotfiles_root if dotfiles_root is not None else common.find_dotfiles_root()
    if root is None:
        return post_apply_outcome.PostApplyOutcome(
            failure="dotfilesの作業ツリーを解決できない",
            next_action="dotfilesの作業ツリーで dotfiles-post-apply を再実行してください。",
        )
    settings_path = (home if home is not None else Path.home()) / ".gemini" / "settings.json"
    managed = json.loads((root / "share/gemini_settings_json_managed.json").read_text(encoding="utf-8"))
    data = pytilpack.jsonc.loads(settings_path.read_text(encoding="utf-8")) if settings_path.exists() else {}
    if not isinstance(data, dict):
        raise ValueError("Gemini settings.jsonのルートにはJSONオブジェクトが必要です。")
    original = copy.deepcopy(data)
    # generalの管理値が旧experimentalの値より優先する。旧設定の他の項目も保持する。
    for section, values in managed.items():
        if isinstance(values, dict) and isinstance(data.get(section), dict):
            data[section].update(values)
        else:
            data[section] = values
    short = log_format.home_short(settings_path)
    if data == original:
        logger.info(log_format.format_status(short, "変更なし"))
        return post_apply_outcome.PostApplyOutcome()
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    if not common.write_settings_hybrid(settings_path, original, data, tag=short):
        return post_apply_outcome.PostApplyOutcome(
            failure=f"{short} の書き込みに失敗",
            next_action=f"{settings_path} と親ディレクトリの書込権限を確認し、dotfiles-post-apply を再実行してください。",
        )
    logger.info(log_format.format_status(short, "更新しました"))
    return post_apply_outcome.PostApplyOutcome(changed=True)

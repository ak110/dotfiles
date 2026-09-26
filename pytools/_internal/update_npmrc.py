"""npmとpnpmの公開待機（サプライチェーン対策）の設定を管理するモジュール。

npmとpnpmは公開待機のキー名と単位が異なる。npmは`~/.npmrc`の`min-release-age`（日数）を読み、
pnpmのキー`minimum-release-age`（分）を`~/.npmrc`へ書くとnpmが未知の設定として警告する。
このためnpmのキーは`~/.npmrc`へ、pnpmのキーはpnpmのグローバル設定へ書き分ける。
詳細は`docs/guide/security.md`を参照。
"""

import logging
import re
import shutil
import sys
from pathlib import Path

from pytools._internal import claude_common, log_format
from pytools._internal.cli import setup_logging

logger = logging.getLogger(__name__)

_NPM_KEY = "min-release-age"
_NPM_VALUE = "1"
_NPM_LINE = f"{_NPM_KEY}={_NPM_VALUE}"
_NPM_PATTERN = re.compile(rf"^{re.escape(_NPM_KEY)}=.*$", re.MULTILINE)
_PNPM_KEY = "minimum-release-age"
_PNPM_VALUE = "1440"
# 以前`~/.npmrc`へ書いていたpnpmのキー。npmの警告を止めるため行ごと除く。
_LEGACY_NPMRC_PATTERN = re.compile(rf"^{re.escape(_PNPM_KEY)}=.*(?:\n|$)", re.MULTILINE)
_PNPM_TIMEOUT = 60.0


def main() -> None:
    """スタンドアロン実行用エントリポイント。"""
    setup_logging()
    run()
    sys.exit(0)


def run(npmrc_path: Path | None = None) -> bool:
    """`~/.npmrc`へnpmの公開待機を設定し、pnpmがあればpnpmのグローバル設定へも設定する。

    Args:
        npmrc_path: 対象パス。None の場合は `~/.npmrc` を使用 (テスト時に差し替え可能)。

    Returns:
        `~/.npmrc`とpnpmのグローバル設定のいずれかを書き換えたかどうか。
    """
    path = npmrc_path if npmrc_path is not None else Path.home() / ".npmrc"
    npmrc_changed = _update_npmrc(path)
    pnpm_changed = _update_pnpm_global()
    return npmrc_changed or pnpm_changed


def _update_npmrc(path: Path) -> bool:
    """`~/.npmrc`へnpmのキーを設定し、pnpmのキーの行を除く。"""
    short = log_format.home_short(path)
    existed = path.exists()
    content = path.read_text(encoding="utf-8") if existed else ""
    new_content = _LEGACY_NPMRC_PATTERN.sub("", content)
    if _NPM_PATTERN.search(new_content):
        new_content = _NPM_PATTERN.sub(_NPM_LINE, new_content)
    else:
        suffix = "" if new_content.endswith("\n") or new_content == "" else "\n"
        new_content = new_content + suffix + _NPM_LINE + "\n"
    if new_content == content:
        logger.info(log_format.format_status(short, f"{_NPM_LINE} は既に設定済み"))
        return False
    path.write_text(new_content, encoding="utf-8")
    if existed:
        logger.info(log_format.format_status(short, f"{_NPM_LINE} を設定しました"))
    else:
        # 「作成し」の目的語をファイルにするため`<対象>: <状態>`の形を使わず、字下げだけを`format_status`とそろえる。
        logger.info(f"    {short} を作成し {_NPM_LINE} を設定しました")
    return True


def _update_pnpm_global() -> bool:
    """pnpmのグローバル設定へ公開待機を設定する。

    `pnpm config set`の終了コードからは変更の有無を判定できないため、事前に現在値を取得して比べる。
    設定ファイルの場所はOSごとにpnpm自身が解決する。
    """
    pnpm = shutil.which("pnpm")
    if pnpm is None:
        logger.info(log_format.format_status("pnpm", "未検出のため公開待機の設定を省略しました"))
        return False
    current = claude_common.run_subprocess(
        [pnpm, "config", "get", "--location", "global", _PNPM_KEY], timeout=_PNPM_TIMEOUT, tag="pnpm"
    )
    if current is None or current.returncode != 0:
        detail = "" if current is None else f": {current.stderr.strip()}"
        logger.warning(log_format.format_status("pnpm", f"{_PNPM_KEY} の取得に失敗しました{detail}"))
        return False
    # 更新通知などが先に出る場合があるため、最後の行を値として読む。
    lines = current.stdout.strip().splitlines()
    if lines and lines[-1].strip() == _PNPM_VALUE:
        logger.info(log_format.format_status("pnpm", f"{_PNPM_KEY}={_PNPM_VALUE} は既に設定済み"))
        return False
    result = claude_common.run_subprocess(
        [pnpm, "config", "set", "--location", "global", _PNPM_KEY, _PNPM_VALUE], timeout=_PNPM_TIMEOUT, tag="pnpm"
    )
    if result is None or result.returncode != 0:
        detail = "" if result is None else f": {result.stderr.strip()}"
        logger.warning(log_format.format_status("pnpm", f"{_PNPM_KEY} の設定に失敗しました{detail}"))
        return False
    logger.info(log_format.format_status("pnpm", f"グローバル設定へ {_PNPM_KEY}={_PNPM_VALUE} を設定しました"))
    return True


if __name__ == "__main__":
    main()

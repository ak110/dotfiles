r"""`share/user.env`の値をユーザー環境変数へ反映する（Windowsのみ）。

Linuxでは`~/.bashrc`が`set -a`の下で同じファイルを読み込む。
Windowsには対応する読み込み経路が無いため、`chezmoi apply`後処理で`HKCU\Environment`へ書き込む。
現在値と異なる値だけを書き込み、書き込んだ場合だけ環境変数の変更を通知する。
"""

import logging
import re
from pathlib import Path

from pytools._internal import common, log_format, post_apply_outcome, winutils

logger = logging.getLogger(__name__)

USER_ENV_RELATIVE = Path("share") / "user.env"


_LINE_PATTERN = re.compile(r"^(?P<key>[A-Za-z_][A-Za-z0-9_]*)=(?P<value>[^\s'\"`$\\#;&|<>()]*)$")
# 過去に配布し、現在は配布をやめた変数と、その配布値。配布値のまま残るユーザー環境変数だけを削除する。
# `PYTHONDONTWRITEBYTECODE`は、hookとCLIが起動のたびに全モジュールをコンパイルし直す原因になったため配布をやめた。
_FORMER_VALUES: dict[str, str] = {"DISABLE_TELEMETRY": "1", "PYTHONDONTWRITEBYTECODE": "1"}


def parse_user_env(text: str) -> list[tuple[str, str]]:
    entries: list[tuple[str, str]] = []
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip() or line.startswith("#"):
            continue
        match = _LINE_PATTERN.match(line)
        if match is None:
            raise ValueError(f"{USER_ENV_RELATIVE.as_posix()} の{number}行目が`KEY=VALUE`の書式に従わない: {line}")
        entries.append((match["key"], match["value"]))
    return entries


def run() -> post_apply_outcome.PostApplyOutcome:
    """`share/user.env`の値のうち現在値と異なるものをユーザー環境変数へ書き込む。

    ファイルを解釈できない場合と書き込みの失敗は失敗と数える。
    """
    root = common.find_dotfiles_root()
    if root is None:
        logger.info(
            log_format.format_status("user.env", "dotfiles ルートが見つからずスキップ。配布元が無いため対応不要"),
        )
        return post_apply_outcome.PostApplyOutcome()
    path = root / USER_ENV_RELATIVE
    if not path.is_file():
        logger.info(log_format.format_status("user.env", f"{path} が無いためスキップ。配布元が無いため対応不要"))
        return post_apply_outcome.PostApplyOutcome()
    try:
        entries = parse_user_env(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        return post_apply_outcome.PostApplyOutcome(
            failure=str(error),
            next_action="問題を解消した後に`update-dotfiles`を再実行する。",
        )

    changed = False
    failures: list[str] = []
    current_keys = {key for key, _value in entries}
    for name, former in _FORMER_VALUES.items():
        if name in current_keys:
            continue
        try:
            previous, _reg_type = winutils.read_user_env_var(name)
            if previous == former:
                wr = winutils.import_winreg()
                with wr.OpenKey(wr.HKEY_CURRENT_USER, "Environment", 0, wr.KEY_SET_VALUE) as key:
                    try:
                        wr.DeleteValue(key, name)
                    except FileNotFoundError:
                        pass
                    else:
                        changed = True
                        logger.info(log_format.format_status("user.env", f"旧{name}設定を削除"))
        except OSError as error:
            failures.append(f"{name} の旧設定削除に失敗: {error}")
    for key, value in entries:
        try:
            current, _reg_type = winutils.read_user_env_var(key)
            if current == value:
                continue
            winutils.write_user_env_var(key, value, winutils.import_winreg().REG_SZ)
        except OSError as error:
            failures.append(f"{key} の設定に失敗: {error}")
            continue
        logger.info(log_format.format_status("user.env", f"{key} を設定"))
        changed = True
    if changed:
        winutils.broadcast_environment_change()
    return post_apply_outcome.PostApplyOutcome(
        changed=changed,
        failure=" / ".join(failures) or None,
        next_action="問題を解消した後に`update-dotfiles`を再実行する。" if failures else None,
    )

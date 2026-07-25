"""systemd user unitの共通セットアップ。"""

import getpass
import logging
import pathlib

from pytools._internal import claude_common, log_format

logger = logging.getLogger(__name__)


def setup(
    *,
    unit_path: pathlib.Path,
    executable_path: pathlib.Path,
    unit_content: str,
    log_tag: str,
    service_name: str,
) -> bool:
    """unitを配置し、サービスを有効化して再起動する。"""
    if not executable_path.is_file():
        logger.info(log_format.format_status(log_tag, f"実行ファイルが未配置: {executable_path}"))
        return False
    changed = False
    try:
        existing = unit_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        existing = None
    if existing != unit_content:
        claude_common.atomic_write_text(unit_path, unit_content, mode=0o644, tag=log_tag)
        logger.info(log_format.format_status(log_tag, f"ユニット配置: {unit_path}"))
        changed = True
    commands: list[tuple[list[str], float, str]] = []
    if changed:
        commands.append((["systemctl", "--user", "daemon-reload"], 15.0, "daemon-reload"))
    commands.extend(
        [
            (["systemctl", "--user", "enable", service_name], 15.0, "enable"),
            (["systemctl", "--user", "restart", service_name], 30.0, "restart"),
        ]
    )
    for command, timeout, label in commands:
        result = claude_common.run_subprocess(command, timeout=timeout, tag=log_tag)
        if result is None or result.returncode != 0:
            return_code = result.returncode if result is not None else "N/A"
            logger.warning(log_format.format_status(log_tag, f"{label}: 失敗 (exit {return_code})"))
    user = getpass.getuser()
    result = claude_common.run_subprocess(["loginctl", "show-user", user, "--property=Linger"], timeout=15.0, tag=log_tag)
    if result is None:
        logger.warning(log_format.format_status(log_tag, "loginctlを実行できないためlinger状態を確認できません"))
    elif result.returncode != 0:
        logger.warning(log_format.format_status(log_tag, f"linger確認: 失敗 (exit {result.returncode})"))
    elif "Linger=no" in result.stdout:
        logger.info(
            log_format.format_status(
                log_tag,
                f"linger 無効: ログアウト中も常駐させるには `sudo loginctl enable-linger {user}` を手動実行する",
            )
        )
    return True

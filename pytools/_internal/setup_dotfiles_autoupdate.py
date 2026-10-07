"""euryale向けdotfiles自動更新タイマーのセットアップ。"""

import logging
import pathlib

from pytools._internal import claude_common, log_format, post_apply_outcome, systemd_user_unit

logger = logging.getLogger(__name__)

_SERVICE_NAME = "dotfiles-autoupdate.service"
_TIMER_NAME = "dotfiles-autoupdate.timer"
_SCRIPT_RELATIVE = pathlib.PurePath("libexec") / "update_dotfiles_if_upstream_changed.py"
_UNIT_DIR_RELATIVE = pathlib.PurePath(".config") / "systemd" / "user"

_SERVICE_UNIT_TEMPLATE = """[Unit]
Description=Update dotfiles when origin/develop changed

[Service]
Type=oneshot
{path_environment}ExecStart={uv} run --no-project --script {script}
"""

_TIMER_UNIT_CONTENT = """[Unit]
Description=Check origin/develop for dotfiles updates

[Timer]
OnStartupSec=1min
OnUnitInactiveSec=10min
Unit=dotfiles-autoupdate.service

[Install]
WantedBy=timers.target
"""


def run() -> post_apply_outcome.PostApplyOutcome:
    """euryaleでdotfiles自動更新用のsystemd user timerを設定する。unitの配置と有効化の失敗は例外として送出する。"""
    if not claude_common.is_euryale():
        return post_apply_outcome.PostApplyOutcome()

    root = claude_common.find_dotfiles_root()
    if root is None:
        logger.info(log_format.format_status("dotfiles-autoupdate", "dotfilesルートを解決できないため設定を見送る"))
        return post_apply_outcome.PostApplyOutcome()

    script = root / _SCRIPT_RELATIVE
    if not script.is_file():
        logger.info(log_format.format_status("dotfiles-autoupdate", f"上流差分確認スクリプトが未配置: {script}"))
        return post_apply_outcome.PostApplyOutcome()

    uv = claude_common.resolve_uv_path()
    if uv is None:
        logger.info(log_format.format_status("dotfiles-autoupdate", "uvが見つからないため設定を見送る"))
        return post_apply_outcome.PostApplyOutcome()

    unit_dir = pathlib.Path.home() / _UNIT_DIR_RELATIVE
    changed = systemd_user_unit.setup_timer(
        service_unit_path=unit_dir / _SERVICE_NAME,
        timer_unit_path=unit_dir / _TIMER_NAME,
        executable_path=uv,
        service_unit_content=_SERVICE_UNIT_TEMPLATE.format(
            path_environment=systemd_user_unit.USER_UNIT_PATH_ENVIRONMENT,
            uv=uv,
            script=script,
        ),
        timer_unit_content=_TIMER_UNIT_CONTENT,
        log_tag="dotfiles-autoupdate",
        timer_name=_TIMER_NAME,
    )
    return post_apply_outcome.PostApplyOutcome(changed=changed)

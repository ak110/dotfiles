"""dotfilesが使うsystemdのユーザータイマーのセットアップ。

全Linuxホストで一時ファイルの経過削除タイマーを有効化し、役割linux_serverのホストでは自動更新タイマーも設定する。
post-applyの工程名は`dotfiles-autoupdate`のまま、経過削除の有効化もこの工程が扱う。
"""

import logging
import os
import pathlib
import shutil

from pytools._internal import common, host_roles, log_format, post_apply_outcome, systemd_user_unit

logger = logging.getLogger(__name__)

_SERVICE_NAME = "dotfiles-autoupdate.service"
_TIMER_NAME = "dotfiles-autoupdate.timer"
_TMPFILES_CLEAN_TIMER_NAME = "systemd-tmpfiles-clean.timer"
_SCRIPT_RELATIVE = pathlib.PurePath("libexec") / "update_dotfiles_if_upstream_changed.py"
_UNIT_DIR_RELATIVE = pathlib.PurePath(".config") / "systemd" / "user"

_SERVICE_UNIT_TEMPLATE = """[Unit]
Description=Update dotfiles when origin/develop changed

[Service]
Type=oneshot
{path_environment}{codex_environment}ExecStart={uv} run --no-project --script {script}
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
    """全Linuxホストで一時ファイルの経過削除タイマーを有効化し、役割linux_serverのホストでは自動更新タイマーも設定する。

    unitの配置と有効化の失敗は例外として送出する。
    ユーザーマネージャーへ接続できない場合は、復旧手順を次の操作とする失敗として返す。
    """
    tmpfiles_changed = _enable_tmpfiles_clean_timer()
    if not host_roles.is_linux_server():
        return post_apply_outcome.PostApplyOutcome(changed=tmpfiles_changed)

    root = common.find_dotfiles_root()
    if root is None:
        logger.info(
            log_format.format_status(
                "dotfiles-autoupdate",
                "dotfilesルートを解決できないため設定を見送る。`update-dotfiles`を再実行して導入と設定をやり直す",
            ),
        )
        return post_apply_outcome.PostApplyOutcome(changed=tmpfiles_changed)

    script = root / _SCRIPT_RELATIVE
    if not script.is_file():
        logger.info(
            log_format.format_status(
                "dotfiles-autoupdate",
                f"上流差分確認スクリプトが未配置: {script}。`update-dotfiles`を再実行して導入と設定をやり直す",
            ),
        )
        return post_apply_outcome.PostApplyOutcome(changed=tmpfiles_changed)

    uv = common.resolve_uv_path()
    if uv is None:
        logger.info(
            log_format.format_status(
                "dotfiles-autoupdate",
                "uvが見つからないため設定を見送る。"
                "https://docs.astral.sh/uv/getting-started/installation/ の手順でuvを導入し、"
                "`update-dotfiles`を再実行する",
            ),
        )
        return post_apply_outcome.PostApplyOutcome(changed=tmpfiles_changed)

    unit_dir = pathlib.Path.home() / _UNIT_DIR_RELATIVE
    try:
        changed = systemd_user_unit.setup_timer(
            service_unit_path=unit_dir / _SERVICE_NAME,
            timer_unit_path=unit_dir / _TIMER_NAME,
            executable_path=uv,
            service_unit_content=_SERVICE_UNIT_TEMPLATE.format(
                path_environment=systemd_user_unit.USER_UNIT_PATH_ENVIRONMENT,
                codex_environment=_codex_environment(),
                uv=uv,
                script=script,
            ),
            timer_unit_content=_TIMER_UNIT_CONTENT,
            log_tag="dotfiles-autoupdate",
            timer_name=_TIMER_NAME,
        )
    except systemd_user_unit.UserManagerUnavailableError as error:
        # 環境の異常のため、汎用の例外経路ではなく復旧手順を次の操作として返す。
        return post_apply_outcome.PostApplyOutcome(failure=str(error), next_action=error.next_action)
    return post_apply_outcome.PostApplyOutcome(changed=changed or tmpfiles_changed)


def _enable_tmpfiles_clean_timer() -> bool:
    """ユーザーの`systemd-tmpfiles-clean.timer`を有効化し、有効化を行った場合にTrueを返す。

    `~/.config/user-tmpfiles.d/dotfiles-tmp.conf`が定める`~/.cache/agent-toolkit/tmp`の経過削除をこのタイマーが実行する。
    systemctlが無い環境とユーザーのsystemdマネージャーへ接続できない環境では見送る。

    Raises:
        systemd_user_unit.SetupError: 有効化のコマンドが失敗した場合に送出する。
    """
    tag = "dotfiles-autoupdate"
    next_action = "systemdのユーザーセッションがある環境で`update-dotfiles`を再実行する"
    if shutil.which("systemctl") is None:
        logger.info(
            log_format.format_status(tag, f"systemctlが無いため一時ファイルの経過削除タイマーの有効化を見送る。{next_action}")
        )
        return False
    manager = common.run_subprocess(["systemctl", "--user", "show-environment"], timeout=15.0, tag=tag)
    if manager is None or manager.returncode != 0:
        # systemdのユーザーセッションを持たないホスト（コンテナーなど）でも配布するため、失敗とせず見送る。
        logger.info(
            log_format.format_status(
                tag,
                "ユーザーのsystemdマネージャーへ接続できないため一時ファイルの経過削除タイマーの有効化を見送る"
                f" ({common.format_cli_error(manager)})。"
                f"ユーザーマネージャーが停止している場合は`sudo systemctl start user@{os.getuid()}.service`で起動し、"
                f"{next_action}",
            ),
        )
        return False
    enabled = common.run_subprocess(["systemctl", "--user", "is-enabled", _TMPFILES_CLEAN_TIMER_NAME], timeout=15.0, tag=tag)
    if enabled is not None and enabled.returncode == 0:
        return False
    result = common.run_subprocess(
        ["systemctl", "--user", "enable", "--now", _TMPFILES_CLEAN_TIMER_NAME], timeout=30.0, tag=tag
    )
    if result is None or result.returncode != 0:
        return_code = result.returncode if result is not None else "N/A"
        raise systemd_user_unit.SetupError(
            f"{_TMPFILES_CLEAN_TIMER_NAME}のenableに失敗しました (exit {return_code}): {common.format_cli_error(result)}。"
            f"`systemctl --user status {_TMPFILES_CLEAN_TIMER_NAME}`で状態を確認し、"
            "原因を直してから`update-dotfiles`を再実行する"
        )
    logger.info(log_format.format_status(tag, f"有効化: {_TMPFILES_CLEAN_TIMER_NAME}"))
    return True


def _codex_environment() -> str:
    """導入時のCODEX_HOMEをsystemdの引用とspecifier規則に合わせて渡す。"""
    value = os.environ.get("CODEX_HOME")
    if not value:
        return ""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%").replace("\n", "\\n").replace("\r", "\\r")
    return f'Environment="CODEX_HOME={escaped}"\n'

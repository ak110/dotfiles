"""Linuxの一時ファイル経過削除タイマーを有効化する。"""

import logging
import os
import pathlib
import shutil

import platformdirs

from pytools._internal import common, log_format, post_apply_outcome, systemd_user_unit

logger = logging.getLogger(__name__)

_TMPFILES_CLEAN_TIMER_NAME = "systemd-tmpfiles-clean.timer"


def run() -> post_apply_outcome.PostApplyOutcome:
    """旧自動更新を撤去し、一時ファイルの経過削除を有効化する。"""
    # 2026-11-08登録。全既存ホストへ撤去が1回反映されたことを確認した後に移行処理を削除する。
    unit_dir = pathlib.Path.home() / ".config/systemd/user"
    units = [unit_dir / name for name in ("dotfiles-autoupdate.timer", "dotfiles-autoupdate.service")]
    state = pathlib.Path(platformdirs.user_state_dir("agent-toolkit", appauthor=False)) / "auto-update"
    changed = False
    if any(path.exists() or path.is_symlink() for path in units):
        timer_exists = units[0].exists() or units[0].is_symlink()
        next_action = (
            "systemdのユーザーセッションがある環境で"
            "`systemctl --user disable --now dotfiles-autoupdate.timer`を実行し、"
            "成功後に`update-dotfiles`を再実行する"
            if timer_exists
            else "`systemctl --user show-environment`で接続を確認してから`update-dotfiles`を再実行する"
        )
        if shutil.which("systemctl") is None:
            return post_apply_outcome.PostApplyOutcome(
                failure=f"systemctlが無いため旧自動更新を撤去できません。残存対象: {units[0]}, {units[1]}, {state}",
                next_action=next_action,
            )
        result = common.run_subprocess(
            ["systemctl", "--user", "disable", "--now", "dotfiles-autoupdate.timer"]
            if timer_exists
            else ["systemctl", "--user", "show-environment"],
            timeout=30.0,
            tag="tmpfiles-clean",
        )
        if result is None or result.returncode != 0:
            return post_apply_outcome.PostApplyOutcome(
                failure=("旧自動更新の停止・無効化に失敗" if timer_exists else "ユーザーsystemdの接続確認に失敗")
                + f": {common.format_cli_error(result)}。"
                f"残存対象: {units[0]}, {units[1]}, {state}",
                next_action=next_action,
            )
        try:
            for path in units:
                path.unlink(missing_ok=True)
                changed = True
        except OSError as error:
            remaining = [str(path) for path in units if path.exists() or path.is_symlink()]
            return post_apply_outcome.PostApplyOutcome(
                changed=changed,
                failure=f"旧自動更新のユニット削除に失敗: {error}。残存対象: {', '.join(remaining)}, {state}",
                next_action="表示されたユニットの権限を確認し、`systemctl --user daemon-reload`と`update-dotfiles`を再実行する",
            )
        result = common.run_subprocess(["systemctl", "--user", "daemon-reload"], timeout=15.0, tag="tmpfiles-clean")
        if result is None or result.returncode != 0:
            return post_apply_outcome.PostApplyOutcome(
                changed=changed,
                failure=(
                    f"旧自動更新のユニット削除後のdaemon-reloadに失敗: {common.format_cli_error(result)}。残存対象: {state}"
                ),
                next_action="`systemctl --user daemon-reload`を成功させてから`update-dotfiles`を再実行する",
            )
    try:
        if state.is_symlink():
            state.unlink()
            changed = True
        elif state.exists():
            shutil.rmtree(state)
            changed = True
    except OSError as error:
        return post_apply_outcome.PostApplyOutcome(
            changed=changed,
            failure=f"旧自動更新の状態削除に失敗: {error}。残存対象: {state}",
            next_action="表示された状態ディレクトリの権限を確認して`update-dotfiles`を再実行する",
        )
    return post_apply_outcome.PostApplyOutcome(changed=_enable_tmpfiles_clean_timer() or changed)


def _enable_tmpfiles_clean_timer() -> bool:
    """ユーザーの経過削除タイマーを有効化する。ユーザーsystemdが無い環境では見送る。"""
    tag = "tmpfiles-clean"
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

"""systemd user unitの共通セットアップ。"""

import getpass
import logging
import pathlib
import time

from pytools._internal import claude_common, log_format

logger = logging.getLogger(__name__)

# restart直後はActiveStateがactivatingのため、初回観測前に待機する。
_SETTLE_SECONDS = 2.0
_POLL_SECONDS = 1.0
_ACTIVE_TIMEOUT_SECONDS = 30.0
# unit本文のRestartSecより長い間隔を空けて2回目を観測する。
# 起動直後に異常終了して再起動を繰り返すサービスは、1回目にactiveを観測できても
# 2回目までにNRestartsが増えるため、2回の観測で常駐可否を判定できる。
_CONFIRM_SECONDS = 6.0


class SetupError(RuntimeError):
    """サービスが常駐状態に至らなかったことを表す。"""


def setup(
    *,
    unit_path: pathlib.Path,
    executable_path: pathlib.Path,
    unit_content: str,
    log_tag: str,
    service_name: str,
) -> bool:
    """unitを配置し、サービスを有効化して再起動する。

    Returns:
        実行ファイル不在で何もしなかった場合False、unit配置とrestartを実施した場合True。

    Raises:
        SetupError: systemctl呼び出しが失敗した場合、
            又はrestart後にサービスが常駐状態へ至らない場合に送出する。
    """
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
    # systemctlの失敗は後続の常駐確認を無意味にする（旧プロセスがactiveのまま残ると
    # NRestartsも変化せず成功と誤判定するため）。失敗した時点で例外を送出して打ち切る。
    for command, timeout, label in commands:
        result = claude_common.run_subprocess(command, timeout=timeout, tag=log_tag)
        if result is None or result.returncode != 0:
            return_code = result.returncode if result is not None else "N/A"
            raise SetupError(f"{service_name}の{label}に失敗しました (exit {return_code})")
    _wait_until_running(service_name=service_name, log_tag=log_tag)
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


def setup_timer(
    *,
    service_unit_path: pathlib.Path,
    timer_unit_path: pathlib.Path,
    executable_path: pathlib.Path,
    service_unit_content: str,
    timer_unit_content: str,
    log_tag: str,
    timer_name: str,
) -> bool:
    """Oneshot serviceとtimerを配置し、timerを有効化して再起動する。

    タイマーは待機状態でactiveとなり再起動回数を持たないため、常駐サービス向けの
    ActiveStateとNRestartsの二重観測は適用せず、再起動後のActiveStateを1回確認する。

    Returns:
        実行ファイル不在で何もしなかった場合False、unit配置とrestartを実施した場合True。

    Raises:
        SetupError: systemctl呼び出しが失敗した場合、又はtimerがactiveでない場合に送出する。
    """
    if not executable_path.is_file():
        logger.info(log_format.format_status(log_tag, f"実行ファイルが未配置: {executable_path}"))
        return False

    changed = False
    for unit_path, unit_content in (
        (service_unit_path, service_unit_content),
        (timer_unit_path, timer_unit_content),
    ):
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
            (["systemctl", "--user", "enable", timer_name], 15.0, "enable"),
            (["systemctl", "--user", "restart", timer_name], 30.0, "restart"),
        ]
    )
    for command, timeout, label in commands:
        result = claude_common.run_subprocess(command, timeout=timeout, tag=log_tag)
        if result is None or result.returncode != 0:
            return_code = result.returncode if result is not None else "N/A"
            raise SetupError(f"{timer_name}の{label}に失敗しました (exit {return_code})")

    result = claude_common.run_subprocess(
        ["systemctl", "--user", "show", timer_name, "--property=ActiveState"],
        timeout=15.0,
        tag=log_tag,
    )
    if result is None or result.returncode != 0:
        raise SetupError(f"{timer_name}の状態を取得できません")
    active_state = ""
    for line in result.stdout.splitlines():
        key, separator, value = line.partition("=")
        if separator and key.strip() == "ActiveState":
            active_state = value.strip()
            break
    if active_state != "active":
        raise SetupError(f"{timer_name}が起動しません: ActiveState={active_state}")
    logger.info(log_format.format_status(log_tag, f"稼働確認: {timer_name}"))

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


def _query_service(service_name: str, log_tag: str) -> tuple[str, str]:
    """サービスのActiveStateとNRestartsを取得する。

    Raises:
        SetupError: systemctlを実行できない場合に送出する。
    """
    result = claude_common.run_subprocess(
        ["systemctl", "--user", "show", service_name, "--property=ActiveState", "--property=NRestarts"],
        timeout=15.0,
        tag=log_tag,
    )
    if result is None or result.returncode != 0:
        raise SetupError(f"{service_name}の状態を取得できません")
    values: dict[str, str] = {}
    for line in result.stdout.splitlines():
        key, separator, value = line.partition("=")
        if separator:
            values[key.strip()] = value.strip()
    return values.get("ActiveState", ""), values.get("NRestarts", "")


def _wait_until_running(*, service_name: str, log_tag: str) -> None:
    """restart後にサービスが常駐することを確認する。

    Raises:
        SetupError: 制限時間内にactiveへ至らない場合、又は再起動を繰り返す場合に送出する。
    """
    time.sleep(_SETTLE_SECONDS)
    deadline = time.monotonic() + _ACTIVE_TIMEOUT_SECONDS
    while True:
        state, restarts = _query_service(service_name, log_tag)
        if state == "active":
            break
        if time.monotonic() >= deadline:
            raise SetupError(f"{service_name}が起動しません: ActiveState={state}")
        time.sleep(_POLL_SECONDS)
    time.sleep(_CONFIRM_SECONDS)
    confirmed_state, confirmed_restarts = _query_service(service_name, log_tag)
    if confirmed_state != "active" or confirmed_restarts != restarts:
        raise SetupError(
            f"{service_name}が常駐しません: ActiveState={confirmed_state}, NRestarts={restarts} から {confirmed_restarts}"
        )
    logger.info(log_format.format_status(log_tag, f"稼働確認: {service_name}"))

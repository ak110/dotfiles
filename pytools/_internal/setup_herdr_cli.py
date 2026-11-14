"""Herdr公式直接インストール版を導入または更新する。"""

import collections.abc
import contextlib
import errno
import logging
import ntpath
import os
import stat
import sys
import tempfile
from pathlib import Path, PureWindowsPath

from agent_toolkit._common import dotfiles_mode

# ヘルプや他の処理で使わない依存は、使用時まで読み込まない。
lazy import httpx

from pytools._internal import common, log_format, post_apply_outcome, setup_cli_common

logger = logging.getLogger(__name__)

_COMMAND_TIMEOUT = 300.0
_TAG = "herdr"
_POSIX_INSTALLER_URL = "https://herdr.dev/install.sh"
_WINDOWS_INSTALLER_URL = "https://herdr.dev/install.ps1"
_DETACHED_UPDATE_MARKER = "outside herdr after detaching from the session"
# 稼働中のサーバーの再起動を要する更新で、非対話の`herdr update`が導入せずに終了0で返すときの診断。
_NOT_UPDATED_MARKER = "Herdr was not updated."


def _launcher_path() -> Path:
    """公式直接インストール版Herdrの安定したランチャー位置を返す。"""
    if value := os.environ.get("HERDR_INSTALL_DIR"):
        return Path(value) / ("herdr.exe" if sys.platform == "win32" else "herdr")
    if sys.platform == "win32":
        local_app_data = os.environ.get("LOCALAPPDATA")
        base = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
        return base / "Programs" / "Herdr" / "bin" / "herdr.exe"
    return Path.home() / ".local" / "bin" / "herdr"


def _comparison_path(path: Path) -> PureWindowsPath:
    """実体検証後のWindowsパスをnamespace表記に依存せず比較する。"""
    value = str(path)
    if value[:8].lower() == "\\\\?\\unc\\":
        value = "\\\\" + value[8:]
    elif value.startswith("\\\\?\\"):
        value = value[4:]
    return PureWindowsPath(ntpath.normpath(value))


def _find_launcher() -> Path | None:
    """所有する版実体をリンクの格納情報から選ぶ。"""
    visible = _launcher_path()
    if sys.platform != "win32":
        return visible if visible.is_file() else None
    home = Path(os.environ.get("HERDR_HOME") or Path.home() / ".herdr")
    standalone = home / "packages" / "standalone"
    for link in (standalone / "current", visible.parent):
        try:
            try:
                stored = link.readlink()
            except OSError as error:
                if error.errno == errno.EINVAL:
                    continue
                raise
            release = (stored if stored.is_absolute() else link.parent / stored).resolve(strict=True)
            releases = (standalone / "releases").resolve(strict=True)
            executable = (release / "herdr.exe").resolve(strict=True)
            if (
                _comparison_path(release) != _comparison_path(releases)
                and _comparison_path(release).is_relative_to(_comparison_path(releases))
                and _comparison_path(executable).is_relative_to(_comparison_path(release))
                and stat.S_ISREG(executable.stat().st_mode)
            ):
                return executable
        except OSError as error:
            if error.errno not in {errno.ENOENT, errno.ENOTDIR, errno.ELOOP}:
                raise
            continue
        except RuntimeError:
            continue
    return None


def run(client: httpx.Client | None = None) -> post_apply_outcome.PostApplyOutcome:
    """公式直接インストール版Herdrを導入または更新し、実行可能な状態を確認する。

    取得・導入・更新の失敗は警告を出力してスキップと数える。
    セッション内の自己更新拒否と、稼働中のサーバーの再起動を要するため導入しなかった更新は延期として返し、
    セッション終了後の手動更新を案内する。
    """
    if dotfiles_mode.is_minimal():
        return post_apply_outcome.PostApplyOutcome()
    with _curl_env_overrides() as env_overrides:
        return _run(client, env_overrides)


@contextlib.contextmanager
def _curl_env_overrides() -> collections.abc.Generator[dict[str, str] | None]:
    """Windowsで失効確認先の不達を許すcurl設定を用意し、子プロセスへ渡す環境上書きを返す。

    curlは`-q`が無いと最初に`CURL_HOME`配下の`.curlrc`を読む。公式インストーラーに加えて
    `herdr update`自身もmanifestと配布物の取得で`-q`を付けずに`curl`を起動するため、
    分岐ごとに用意せずステップ全体の子プロセスへ同じ設定を渡す。
    一時ディレクトリは終了時（例外の送出時を含む）に除去し、親プロセスの環境は変えない。
    """
    if sys.platform != "win32":
        yield None
        return
    with tempfile.TemporaryDirectory(prefix="herdr-curl-") as curl_home:
        (Path(curl_home) / ".curlrc").write_text("ssl-revoke-best-effort\n", encoding="ascii")
        yield {"CURL_HOME": curl_home}


def _run(client: httpx.Client | None, env_overrides: dict[str, str] | None) -> post_apply_outcome.PostApplyOutcome:
    launcher = _find_launcher()
    update_deferred = False
    restart_deferred = False
    if launcher is not None:
        result = common.run_subprocess(
            [str(launcher), "update"], timeout=_COMMAND_TIMEOUT, tag=_TAG, env_overrides=env_overrides
        )
        update_error = result.stderr if result is not None and isinstance(result.stderr, str) else ""
        # 未知の更新失敗を隠さず、Herdrが離脱後の自己更新を明示した診断だけを保留対象にする。
        update_deferred = (
            result is not None
            and result.returncode != 0
            and "herdr update" in update_error
            and _DETACHED_UPDATE_MARKER in update_error
        )
        restart_deferred = result is not None and result.returncode == 0 and _NOT_UPDATED_MARKER in update_error
    else:
        result, reason = setup_cli_common.run_official_installer(
            client,
            posix_url=_POSIX_INSTALLER_URL,
            windows_url=_WINDOWS_INSTALLER_URL,
            tag=_TAG,
            timeout=_COMMAND_TIMEOUT,
            env_overrides=env_overrides,
        )
        if reason:
            logger.warning(log_format.format_status(_TAG, f"{reason}。`update-dotfiles`を再実行する"))
            return post_apply_outcome.PostApplyOutcome()
        launcher = _find_launcher()
    if result is None or (result.returncode != 0 and not update_deferred):
        logger.warning(
            log_format.format_status(
                _TAG,
                f"導入または更新に失敗: {common.format_cli_error(result)}。`update-dotfiles`を再実行する",
            ),
        )
        return post_apply_outcome.PostApplyOutcome()
    verification = (
        common.run_subprocess([str(launcher), "--version"], timeout=30, tag=_TAG, env_overrides=env_overrides)
        if launcher is not None
        else None
    )
    if verification is None or verification.returncode != 0:
        detail = common.format_cli_error(verification)
        logger.warning(
            log_format.format_status(_TAG, f"導入または更新後の確認に失敗: {detail}。`update-dotfiles`を再実行する"),
        )
        return post_apply_outcome.PostApplyOutcome()
    setup_cli_common.prepend_path(_launcher_path().parent)
    if update_deferred or restart_deferred:
        return post_apply_outcome.PostApplyOutcome(
            changed=False,
            notices=(_deferred_notice(inside_session=update_deferred),),
            deferred=True,
        )
    return post_apply_outcome.PostApplyOutcome(changed=True)


def _deferred_notice(*, inside_session: bool) -> post_apply_outcome.PostApplyNotice:
    """保留した更新の理由と手動の操作を案内する。"""
    if inside_session:
        reason = "Herdrセッション内では自己更新できないため、更新を保留しました。"
        manual = "セッションから離脱した後、Herdr外の端末で次のコマンドを実行する。"
    else:
        reason = "稼働中のHerdrサーバーの再起動を要する更新のため、稼働中のセッションを保って更新を保留しました。"
        manual = "Herdrのセッションを全て終了してから、次のコマンドを実行する。"
    return post_apply_outcome.PostApplyNotice(message=reason + manual, command="herdr update")

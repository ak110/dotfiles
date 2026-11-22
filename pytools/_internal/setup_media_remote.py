r"""メディアリモコン自動起動セットアップ（Windows / 役割media_remoteのホストのみ）。

Windowsスタートアップフォルダー
（`%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup`）配下に
`dotfiles-media-remote.lnk`を冪等配置する。

`.lnk`は`wscript.exe`経由でVBSラッパー
（`%LOCALAPPDATA%\dotfiles\media-remote\launch.vbs`）を起動し、
VBSラッパーが`dotfiles-media-remote.exe serve`を非表示ウィンドウで実行する。
これによりコンソール窓・タスクバーアイコンを抑止しつつ、
uv tool venvの`dotfiles-media-remote.exe`を確実に解決する。
役割media_remoteを持たないホストでは既存のショートカットとVBSラッパーを削除する。

役割media_remoteのホストでは配置に続けてmedia-remoteを再起動する。
post-applyテンプレートがpytoolsの再導入のために停止した場合は起動だけを行い、
再導入しない回は停止→起動を行う。後者はハングして応答しなくなったmedia-remoteを回復する対症療法であり、
ハングの原因は未特定である。`launch.vbs`と実行ファイルのパスは本モジュールだけが持つ。
"""

import base64
import logging
import os
import pathlib
import uuid

lazy import psutil

from pytools._internal import common, host_roles, log_format, post_apply_outcome

logger = logging.getLogger(__name__)

LNK_NAME = "dotfiles-media-remote.lnk"
WSCRIPT_PATH = r"C:\Windows\System32\wscript.exe"

STOPPED_ENV = "DOTFILES_MEDIA_REMOTE_STOPPED"
REINSTALL_ENV = "DOTFILES_PYTOOLS_REINSTALL"

_STOP_TIMEOUT_SEC = 5.0


def run() -> post_apply_outcome.PostApplyOutcome:
    """役割media_remoteのホストの場合のみショートカット配置、それ以外では既存ショートカットを削除する。

    役割media_remoteのホストでは続けてmedia-remoteを再起動する。ショートカットとVBSラッパーの配置と削除、
    media-remoteの停止と起動の失敗は失敗と数える。
    """
    startup_dir = _startup_dir()
    if not startup_dir.is_dir():
        logger.info(
            log_format.format_status(
                "media-remote",
                f"スタートアップ未存在: {startup_dir}。このWindowsフォルダーが無い環境では対応不要",
            ),
        )
        return post_apply_outcome.PostApplyOutcome()
    lnk = startup_dir / LNK_NAME
    vbs = _vbs_path()

    try:
        if not host_roles.has_role(host_roles.MEDIA_REMOTE):
            return post_apply_outcome.PostApplyOutcome(changed=_ensure_absent(lnk, vbs))

        exe = _find_media_remote_exe()
        if exe is None:
            logger.info(
                log_format.format_status(
                    "media-remote",
                    "dotfiles-media-remote.exeが見つからないためスキップ。`update-dotfiles`を再実行してpytoolsを導入する",
                ),
                extra=log_format.SHOW,
            )
            return post_apply_outcome.PostApplyOutcome()
        vbs_changed = _ensure_vbs(vbs, exe)
        lnk_changed = _ensure_shortcut(lnk, vbs)
        _restart(vbs, exe)
    except OSError as error:
        return post_apply_outcome.PostApplyOutcome(
            failure=str(error),
            next_action="問題を解消した後に`update-dotfiles`を再実行する。",
        )
    return post_apply_outcome.PostApplyOutcome(changed=vbs_changed or lnk_changed)


def _restart(vbs: pathlib.Path, exe: pathlib.Path) -> None:
    if os.environ.get(STOPPED_ENV) == "1":
        _start(vbs, exe)
        return
    if os.environ.get(REINSTALL_ENV) == "1":
        return
    _stop_running(exe)
    _start(vbs, exe)


def _stop_running(exe: pathlib.Path) -> None:
    targets: list[psutil.Process] = []

    for listed in psutil.process_iter():
        try:
            process = psutil.Process(listed.pid)
            info = process.as_dict(["exe", "cmdline"])
        except psutil.NoSuchProcess:
            continue
        if _is_media_remote(info, exe):
            targets.append(process)
    logger.info(log_format.format_status("media-remote", f"対症療法の停止対象プロセス: {len(targets)}件"))
    for process in targets:
        logger.info(log_format.format_status("media-remote", f"PID={process.pid} を停止"))
        try:
            process.kill()
        except psutil.NoSuchProcess:
            continue
        except psutil.Error as error:
            raise OSError(f"media-remote (PID={process.pid}) を停止できない: {error}") from error
    _gone, alive = psutil.wait_procs(targets, timeout=_STOP_TIMEOUT_SEC)
    if alive:
        raise OSError(f"media-remote が停止しない: PID={', '.join(str(process.pid) for process in alive)}")


def _is_media_remote(info: dict[str, object], exe: pathlib.Path) -> bool:
    exe_text = str(exe).casefold()
    process_exe = info.get("exe")
    if isinstance(process_exe, str) and process_exe.casefold() == exe_text:
        return True
    cmdline = info.get("cmdline")
    if not isinstance(cmdline, list):
        return False
    joined = " ".join(str(part) for part in cmdline)
    return exe_text in joined.casefold() or "pytools.media_remote" in joined


def _start(vbs: pathlib.Path, exe: pathlib.Path) -> None:
    if not vbs.is_file():
        raise OSError(f"常駐の起動に必要なVBSがありません: {vbs}")
    arguments = _shortcut_arguments(vbs)
    task_name = f"dotfiles-media-remote-{uuid.uuid4().hex}"
    # DETACHED_PROCESSだけではWindows OpenSSHの起動単位から独立せず、CIM起動ではSession0になる。
    # 一時タスクに既存VBSを実行させることで、非表示・対話セッション・接続終了後の寿命をそろえる。
    script = f"""
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$identity = [System.Security.Principal.WindowsIdentity]::GetCurrent()
$sessions = @(Get-CimInstance Win32_Process -Filter "Name='explorer.exe'" | ForEach-Object {{
    $owner = Invoke-CimMethod -InputObject $_ -MethodName GetOwnerSid
    if ($owner.Sid -eq $identity.User.Value -and $_.SessionId -ne 0) {{ $_.SessionId }}
}})
if (-not $sessions.Count) {{ throw '同じユーザーの対話セッションがありません。Windowsへログオンして再実行してください。' }}
$name = '{task_name}'
$registered = $false
try {{
    $action = New-ScheduledTaskAction -Execute '{_ps_escape(WSCRIPT_PATH)}' -Argument '{_ps_escape(arguments)}'
    $principal = New-ScheduledTaskPrincipal -UserId $identity.Name -LogonType Interactive -RunLevel Limited
    $settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) `
        -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
    Register-ScheduledTask -TaskName $name -Action $action -Principal $principal -Settings $settings | Out-Null
    $registered = $true
    Start-ScheduledTask -TaskName $name
    $deadline = [DateTime]::UtcNow.AddSeconds(10)
    do {{
        $task = Get-ScheduledTask -TaskName $name
        $info = Get-ScheduledTaskInfo -TaskName $name
        $running = @(Get-CimInstance Win32_Process -Filter "Name='dotfiles-media-remote.exe'" | Where-Object {{
            $_.ExecutablePath -eq '{_ps_escape(str(exe))}' -and $_.SessionId -in $sessions
        }})
        $complete = $task.State -eq 'Ready' -and $info.LastTaskResult -eq 0 -and $info.LastRunTime.Year -gt 2000
        if ($running.Count -and $complete) {{ break }}
        if ($info.LastRunTime.Year -gt 2000 -and $task.State -eq 'Ready' -and $info.LastTaskResult -ne 0) {{
            throw "常駐の起動に失敗しました: task result=$($info.LastTaskResult)"
        }}
        if ([DateTime]::UtcNow -ge $deadline) {{ throw '対話セッションでメディアリモコンの起動を確認できません。' }}
        Start-Sleep -Milliseconds 100
    }} while ($true)
}} finally {{
    if ($registered) {{ Unregister-ScheduledTask -TaskName $name -Confirm:$false }}
}}
"""
    logger.info(log_format.format_status("media-remote", "dotfiles-media-remote を再起動"))
    result = common.run_subprocess(
        [
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-EncodedCommand",
            base64.b64encode(script.encode("utf-16le")).decode("ascii"),
        ],
        timeout=30.0,
        tag="media-remote",
    )
    if result is None or result.returncode != 0:
        raise OSError(f"対話セッションでmedia-remoteを起動できない: {common.format_cli_error(result)}")


def _startup_dir() -> pathlib.Path:
    appdata = os.environ.get("APPDATA")
    base = pathlib.Path(appdata) if appdata else pathlib.Path.home() / "AppData" / "Roaming"
    return base / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def _vbs_path() -> pathlib.Path:
    local = os.environ.get("LOCALAPPDATA")
    base = pathlib.Path(local) if local else pathlib.Path.home() / "AppData" / "Local"
    return base / "dotfiles" / "media-remote" / "launch.vbs"


def _find_media_remote_exe() -> pathlib.Path | None:
    candidate = pathlib.Path.home() / ".local" / "bin" / "dotfiles-media-remote.exe"
    if candidate.is_file():
        return candidate
    return None


def _ensure_absent(lnk: pathlib.Path, vbs: pathlib.Path) -> bool:
    changed = False
    for path in (lnk, vbs):
        if not path.is_file():
            continue
        try:
            path.unlink()
            logger.info(log_format.format_status("media-remote", f"対象外ホストのため削除: {path}"))
            changed = True
        except OSError as e:
            raise OSError(f"{path} の削除に失敗: {e}") from e
    return changed


def _build_vbs_content(exe: pathlib.Path) -> str:
    """VBSラッパー本文を生成する。

    `WScript.Shell.Run`の第1引数（コマンド文字列）でexe絶対パスをダブルクオートで
    囲む。VBS文字列リテラル内のダブルクオートは`""`でエスケープする。
    `WindowStyle=0`で非表示、`bWaitOnReturn=False`で即時復帰する。
    """
    exe_str = str(exe).replace('"', '""')
    return f'CreateObject("WScript.Shell").Run """{exe_str}"" serve", 0, False\n'


def _ensure_vbs(vbs: pathlib.Path, exe: pathlib.Path) -> bool:
    desired = _build_vbs_content(exe)
    if vbs.is_file() and vbs.read_text(encoding="utf-8") == desired:
        return False
    vbs.parent.mkdir(parents=True, exist_ok=True)
    vbs.write_text(desired, encoding="utf-8")
    logger.info(log_format.format_status("media-remote", f"VBSラッパー配置: {vbs}"))
    return True


def _ensure_shortcut(lnk: pathlib.Path, vbs: pathlib.Path) -> bool:
    if _is_up_to_date(lnk, vbs):
        return False
    if not _create_shortcut(lnk, vbs):
        raise OSError(f"ショートカット生成に失敗: {lnk}")
    logger.info(log_format.format_status("media-remote", f"ショートカット配置: {lnk}"))
    return True


def _shortcut_arguments(vbs: pathlib.Path) -> str:
    return f'"{vbs}"'


def _is_up_to_date(lnk: pathlib.Path, vbs: pathlib.Path) -> bool:
    if not lnk.is_file():
        return False
    actual = _read_shortcut(lnk)
    if actual is None:
        return False
    actual_target, actual_args = actual
    expected_args = _shortcut_arguments(vbs)
    # Windowsのファイルシステムはcase-insensitiveのため大小文字を揃えて比較する。
    return actual_target.lower() == WSCRIPT_PATH.lower() and actual_args.lower() == expected_args.lower()


def _read_shortcut(lnk: pathlib.Path) -> tuple[str, str] | None:
    """PowerShellで`WScript.Shell` COM経由でTargetPath/Argumentsを取得する。

    タブ文字（`[char]9`）をフィールド区切りに使う。
    Arguments・TargetPathにはタブが現れないため曖昧化しない。
    """
    sep = "\t"
    script = (
        "$ws = New-Object -ComObject WScript.Shell; "
        f"$s = $ws.CreateShortcut('{_ps_escape(str(lnk))}'); "
        "[Console]::Out.Write($s.TargetPath); "
        "[Console]::Out.Write([char]9); "
        "[Console]::Out.Write($s.Arguments)"
    )
    result = common.run_subprocess(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        timeout=30.0,
        tag="media-remote",
    )
    if result is None or result.returncode != 0:
        return None
    parts = result.stdout.split(sep, 1)
    if len(parts) != 2:
        return None
    return parts[0].strip(), parts[1].strip()


def _create_shortcut(lnk: pathlib.Path, vbs: pathlib.Path) -> bool:
    """PowerShellで`WScript.Shell` COM経由でショートカットを生成・上書きする。"""
    home = str(pathlib.Path.home())
    arguments = _shortcut_arguments(vbs)
    script = (
        "$ws = New-Object -ComObject WScript.Shell; "
        f"$s = $ws.CreateShortcut('{_ps_escape(str(lnk))}'); "
        f"$s.TargetPath = '{_ps_escape(WSCRIPT_PATH)}'; "
        f"$s.Arguments = '{_ps_escape(arguments)}'; "
        f"$s.WorkingDirectory = '{_ps_escape(home)}'; "
        "$s.WindowStyle = 7; "
        "$s.Save()"
    )
    result = common.run_subprocess(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        timeout=30.0,
        tag="media-remote",
    )
    return result is not None and result.returncode == 0


def _ps_escape(value: str) -> str:
    """PowerShellシングルクオート文字列内のエスケープ（`'`は`''`で表現する）。"""
    return value.replace("'", "''")

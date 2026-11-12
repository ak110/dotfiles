# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。
"""POSIXとWindowsのprocess-loop監督ランチャーを検証する。"""

import argparse
import json
import os
import pathlib
import subprocess
import sys
from collections.abc import Callable

import pytest

from agent_toolkit import atk  # noqa: E402  # pylint: disable=wrong-import-position
from agent_toolkit._testing.process_loop_update_support import (
    _process_loop_commands_isolated as _process_loop_commands_isolated,
)
from agent_toolkit._testing.process_loop_update_support import _registered_update_boundary as _registered_update_boundary
from agent_toolkit._testing.process_loop_update_support import _resolve_process_loop_commands as _resolve_process_loop_commands


def _run_posix_launcher(
    tmp_path: pathlib.Path,
    *argv: str,
    send_term: bool = False,
    return_code: int | None = None,
    omit_spec: bool = False,
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    """決定論的なuv代用品でPOSIXランチャーを実行する。"""
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir(parents=True)
    call_log = tmp_path / "uv-calls"
    fake_uv = fake_bin / "uv"
    fake_uv.write_text(
        """#!/usr/bin/env python3
import os
import pathlib
import signal
import sys

if sys.argv[-1].endswith("process_loop_log.py"):
    print(os.environ["FAKE_SUPERVISOR_LOG"])
    raise SystemExit(0)

log = pathlib.Path(os.environ["FAKE_UV_LOG"])
calls = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
calls.append(" ".join(sys.argv[1:]))
log.write_text("\\n".join(calls) + "\\n", encoding="utf-8")
spec = os.environ.get("AGENT_TOOLKIT_RESTART_SPEC")
if spec and os.environ.get("FAKE_UV_RETURN_CODE"):
    raise SystemExit(int(os.environ["FAKE_UV_RETURN_CODE"]))
if spec and os.environ.get("FAKE_UV_SEND_TERM") == "1":
    os.kill(os.getppid(), signal.SIGTERM)
    raise SystemExit(0)
if spec and len(calls) == 1:
    if os.environ.get("FAKE_UV_OMIT_SPEC") != "1":
        pathlib.Path(spec).write_text("\\n".join(sys.argv[-3:]) + "\\n", encoding="utf-8")
    raise SystemExit(75)
""",
        encoding="utf-8",
    )
    fake_uv.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = os.pathsep.join((str(fake_bin), env["PATH"]))
    env["FAKE_UV_LOG"] = str(call_log)
    env["FAKE_SUPERVISOR_LOG"] = str(tmp_path / "process-wi.log")
    if send_term:
        env["FAKE_UV_SEND_TERM"] = "1"
    if return_code is not None:
        env["FAKE_UV_RETURN_CODE"] = str(return_code)
    if omit_spec:
        env["FAKE_UV_OMIT_SPEC"] = "1"
    launcher = pathlib.Path(atk.__file__).resolve().parents[1] / "scripts" / "atk"
    result = subprocess.run(
        [str(launcher), *argv],
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    return result, call_log.read_text(encoding="utf-8").splitlines()


def _process_loop_subcommands() -> set[str]:
    """公開CLIのパーサー定義からprocess-loop配下の部分コマンド集合を取得する。"""
    # 公開CLIの登録との一致を調べるため、argparseの登録済みactionを参照する。
    # pylint: disable=protected-access
    parser = atk._build_parser("wi")
    wi_parser = next(action.choices["wi"] for action in parser._actions if isinstance(action, argparse._SubParsersAction))
    loop_parser = next(
        action.choices["process-loop"] for action in wi_parser._actions if isinstance(action, argparse._SubParsersAction)
    )
    return next(set(action.choices) for action in loop_parser._actions if isinstance(action, argparse._SubParsersAction))


def test_posix_launcher_supervises_all_process_loop_subcommands(tmp_path: pathlib.Path) -> None:
    """CLI定義にある全process-loop部分コマンドをPOSIXランチャーが監督する。"""
    for subcommand in _process_loop_subcommands():
        result, uv_calls = _run_posix_launcher(tmp_path / subcommand, "wi", "process-loop", subcommand)

        assert result.returncode == 0, result.stderr
        assert len(uv_calls) == 2


def test_posix_launcher_supervises_registered_loop_options(tmp_path: pathlib.Path) -> None:
    """登録全体の起動オプションを指定した常駐をランチャーが監督する。"""
    result, uv_calls = _run_posix_launcher(tmp_path, "wi", "process-loop", "--no-alerts")

    assert result.returncode == 0, result.stderr
    assert len(uv_calls) == 2


def test_posix_launcher_runs_other_commands_once(tmp_path: pathlib.Path) -> None:
    """process-loop以外と廃止済みmqは監督せず一回だけ実行する。"""
    for argv in (("mq", "process-loop"), ("wi", "list"), ("plans", "process-loop")):
        result, uv_calls = _run_posix_launcher(tmp_path / "-".join(argv), *argv)

        assert result.returncode == 0, result.stderr
        assert len(uv_calls) == 1


def test_windows_launcher_preserves_crlf() -> None:
    """Windowsに配布するバッチファイルのCRLF契約を保つ。"""
    launcher = pathlib.Path(atk.__file__).resolve().parents[1] / "scripts" / "atk.cmd"
    data = launcher.read_bytes()
    assert data.endswith(b"\r\n")
    assert b"\n" not in data.replace(b"\r\n", b"")


@pytest.mark.windows
@pytest.mark.skipif(os.name != "nt", reason="Windowsのcmd/powershellで監督とargvを検証する")
@pytest.mark.parametrize(
    ("argv", "supervised"),
    [
        (["wi", "process-loop", "--no-alerts"], True),
        (["wi", "process-loop", "status"], True),
        (["wi", "list", "value with spaces", "a!b"], False),
        (["mq", "process-loop"], False),
    ],
)
def test_windows_launcher_preserves_arguments_and_restart_result(
    tmp_path: pathlib.Path,
    argv: list[str],
    supervised: bool,
    host_environ: Callable[[], dict[str, str]],
) -> None:
    """公開argvを子へ保ち、監督対象だけ再起動して非対象の終了値を返す。"""
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    log = tmp_path / "calls.json"
    fake_uv = fake_bin / "fake_uv.py"
    fake_uv.write_text(
        "import json, os, pathlib, sys\n"
        "log = pathlib.Path(os.environ['FAKE_UV_LOG'])\n"
        "calls = json.loads(log.read_text(encoding='utf-8')) if log.exists() else []\n"
        "payload = sys.argv[sys.argv.index('--prepare-if-missing') + 1:]\n"
        "calls.append(payload)\n"
        "log.write_text(json.dumps(calls), encoding='utf-8')\n"
        "spec = os.environ.get('AGENT_TOOLKIT_RESTART_SPEC')\n"
        "if spec and len(calls) == 1:\n"
        "    pathlib.Path(spec).write_text('\\n'.join(payload) + '\\n', encoding='utf-8')\n"
        "    raise SystemExit(75)\n"
        "raise SystemExit(0 if spec else 7)\n",
        encoding="utf-8",
    )
    (fake_bin / "uv.cmd").write_bytes(
        f'@echo off\r\n"{sys.executable}" "{fake_uv}" %*\r\nexit /b %ERRORLEVEL%\r\n'.encode("cp932")
    )
    env = host_environ()
    env["TEMP"] = str(tmp_path)
    env["TMP"] = str(tmp_path)
    env.pop("AGENT_TOOLKIT_RESTART_SPEC", None)
    env["PATH"] = os.pathsep.join((str(fake_bin), env["PATH"]))
    env["FAKE_UV_LOG"] = str(log)
    launcher = pathlib.Path(atk.__file__).resolve().parents[1] / "scripts" / "atk.cmd"
    command = f'cmd.exe /d /s /c "{subprocess.list2cmdline([str(launcher), *argv])}"'
    result = subprocess.run(command, env=env, capture_output=True, check=False, timeout=60)
    assert result.returncode == (0 if supervised else 7), result.stderr
    calls = json.loads(log.read_text(encoding="utf-8"))
    assert len(calls) == (2 if supervised else 1)
    assert all(call[1:] == argv for call in calls)


def test_posix_launcher_logs_received_termination(tmp_path: pathlib.Path) -> None:
    result, _calls = _run_posix_launcher(tmp_path, "wi", "process-loop", send_term=True)

    assert result.returncode == 143
    log = (tmp_path / "process-wi.log").read_text(encoding="utf-8")
    assert "event=launcher_exit" in log
    assert "status=143 signal=TERM" in log


@pytest.mark.parametrize(
    ("return_code", "omit_spec", "expected_event"),
    [(2, False, "launcher_child_end"), (None, True, "launcher_restart_spec_missing")],
)
def test_posix_launcher_logs_non_restart_exit(
    tmp_path: pathlib.Path, return_code: int | None, omit_spec: bool, expected_event: str
) -> None:
    result, _calls = _run_posix_launcher(tmp_path, "wi", "process-loop", return_code=return_code, omit_spec=omit_spec)

    assert result.returncode == (75 if omit_spec else 2)
    log = (tmp_path / "process-wi.log").read_text(encoding="utf-8")
    assert f"event={expected_event}" in log

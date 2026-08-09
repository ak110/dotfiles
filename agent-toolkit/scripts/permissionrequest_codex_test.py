"""permissionrequest_codexの厳密一致とfail-open境界を検証する。"""

# pylint: disable=protected-access

from __future__ import annotations

import json
import os
import pathlib
import shlex
import subprocess
import sys
import typing

import _managed_temp
import permissionrequest_codex as subject
import pytest

_ENTRYPOINT = pathlib.Path(__file__).resolve().parent / "claude_hook.py"
_HELPER = pathlib.Path(_managed_temp.__file__).resolve()


@pytest.fixture(autouse=True)
def isolated_state_root(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """外部真正性状態を各テストの専用領域へ分離する。"""
    monkeypatch.setattr(_managed_temp, "_state_root_path", lambda: tmp_path / "external-state")


def _payload(command: str, *, tool_name: str = "Bash") -> str:
    return json.dumps(
        {
            "hook_event_name": "PermissionRequest",
            "tool_name": tool_name,
            "tool_input": {"command": command},
        }
    )


def _command(plugin_root: pathlib.Path, target: pathlib.Path) -> str:
    tokens = [
        "uv",
        "run",
        "--no-project",
        "--script",
        str(plugin_root / "scripts" / "_managed_temp.py"),
        "cleanup",
        "--path",
        str(target),
    ]
    return subprocess.list2cmdline(tokens) if os.name == "nt" else shlex.join(tokens)


def _atk_command(target: pathlib.Path) -> str:
    tokens = ["atk", "managed-temp", "cleanup", "--path", str(target)]
    return subprocess.list2cmdline(tokens) if os.name == "nt" else shlex.join(tokens)


def test_allows_only_valid_managed_cleanup(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    plugin_root = pathlib.Path(__file__).resolve().parent.parent
    temp_root = tmp_path / "temp"
    temp_root.mkdir()
    monkeypatch.setenv("PLUGIN_ROOT", str(plugin_root))
    monkeypatch.setattr(_managed_temp.tempfile, "gettempdir", lambda: str(temp_root))
    target = _managed_temp.create_managed_temp("hook-test")

    assert subject.main(_payload(_command(plugin_root, target))) == 0

    output = json.loads(capsys.readouterr().out)
    assert output == {
        "hookSpecificOutput": {
            "hookEventName": "PermissionRequest",
            "decision": {"behavior": "allow"},
        }
    }
    assert target.exists()


@pytest.mark.parametrize(
    "mutate",
    [
        lambda command: f"{command} && true",
        lambda command: f"env X=1 {command}",
        lambda command: command.replace("cleanup", "create"),
        lambda command: command.replace("--path", "--prefix"),
        lambda command: command.replace("uv run", "uv  run", 1) + " extra",
        lambda command: f"{command} | more",
        lambda command: f"{command}\nver",
        lambda command: f"{command}; ver",
        lambda command: command.replace("uv run", "rm -rf", 1),
        lambda command: f'"{command}',
        lambda command: f'{command}"',
    ],
)
def test_rejects_noncanonical_command(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    mutate: object,
) -> None:
    plugin_root = pathlib.Path(__file__).resolve().parent.parent
    temp_root = tmp_path / "temp"
    temp_root.mkdir()
    monkeypatch.setenv("PLUGIN_ROOT", str(plugin_root))
    monkeypatch.setattr(_managed_temp.tempfile, "gettempdir", lambda: str(temp_root))
    target = _managed_temp.create_managed_temp("hook-test")
    command = typing.cast("typing.Callable[[str], str]", mutate)(_command(plugin_root, target))

    assert subject.main(_payload(command)) == 0
    assert not capsys.readouterr().out
    assert target.exists()


def test_rejects_wrong_tool_event_root_and_unmanaged_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    plugin_root = pathlib.Path(__file__).resolve().parent.parent
    temp_root = tmp_path / "temp"
    temp_root.mkdir()
    monkeypatch.setattr(_managed_temp.tempfile, "gettempdir", lambda: str(temp_root))
    target = _managed_temp.create_managed_temp("hook-test")
    command = _command(plugin_root, target)

    monkeypatch.delenv("PLUGIN_ROOT", raising=False)
    assert subject.main(_payload(command)) == 0
    monkeypatch.setenv("PLUGIN_ROOT", str(plugin_root))
    assert subject.main(_payload(command, tool_name="Write")) == 0
    wrong_event = json.loads(_payload(command))
    wrong_event["hook_event_name"] = "PreToolUse"
    assert subject.main(json.dumps(wrong_event)) == 0
    unmanaged = temp_root / "unmanaged"
    unmanaged.mkdir()
    assert subject.main(_payload(_command(plugin_root, unmanaged))) == 0
    assert not capsys.readouterr().out


def test_rejects_handmade_marker_without_external_state(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """管理対象内のmarkerだけを模造しても承認しない。"""
    plugin_root = pathlib.Path(__file__).resolve().parent.parent
    temp_root = tmp_path / "temp"
    temp_root.mkdir()
    target = temp_root / "handmade"
    target.mkdir(mode=0o700)
    if os.name == "nt":
        _managed_temp._windows_secure_path(target, directory=True)
    marker = _managed_temp._record(target, "0" * 64)
    _managed_temp._write_marker(target, marker)
    monkeypatch.setenv("PLUGIN_ROOT", str(plugin_root))
    monkeypatch.setattr(_managed_temp.tempfile, "gettempdir", lambda: str(temp_root))

    assert subject.main(_payload(_command(plugin_root, target))) == 0
    assert not capsys.readouterr().out
    assert target.exists()


def test_malformed_payload_and_validation_error_return_no_decision(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    plugin_root = pathlib.Path(__file__).resolve().parent.parent
    monkeypatch.setenv("PLUGIN_ROOT", str(plugin_root))
    assert subject.main("not-json") == 0
    assert subject.main("[]") == 0
    assert subject.main(json.dumps({"hook_event_name": "PermissionRequest", "tool_name": "Bash"})) == 0
    assert not capsys.readouterr().out


@pytest.mark.parametrize("launcher_source", ["home", "plugin"])
def test_allows_trusted_atk_launcher_command(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    launcher_source: str,
) -> None:
    """ホーム側ラッパーと現行プラグイン側ランチャーを許可する。"""
    plugin_root = pathlib.Path(__file__).resolve().parent.parent
    temp_root = tmp_path / "temp"
    temp_root.mkdir()
    home = tmp_path / "home"
    launcher_name = "atk.cmd" if os.name == "nt" else "atk"
    home_launcher = home / ".local" / "bin" / launcher_name
    home_launcher.parent.mkdir(parents=True)
    home_launcher.write_text("launcher\n", encoding="utf-8")
    launcher = home_launcher if launcher_source == "home" else plugin_root / "bin" / launcher_name
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("PLUGIN_ROOT", str(plugin_root))
    monkeypatch.setattr(subject.shutil, "which", lambda name: str(launcher) if name == "atk" else None)
    monkeypatch.setattr(_managed_temp.tempfile, "gettempdir", lambda: str(temp_root))
    target = _managed_temp.create_managed_temp("hook-test")

    assert subject.main(_payload(_atk_command(target))) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["hookSpecificOutput"]["decision"] == {"behavior": "allow"}


def test_rejects_untrusted_or_noncanonical_atk_command(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """別ランチャー、追加引数、演算子、相対パス、管理外パスを拒否する。"""
    plugin_root = pathlib.Path(__file__).resolve().parent.parent
    temp_root = tmp_path / "temp"
    temp_root.mkdir()
    other_launcher = tmp_path / ("atk.cmd" if os.name == "nt" else "atk")
    other_launcher.write_text("other\n", encoding="utf-8")
    monkeypatch.setenv("PLUGIN_ROOT", str(plugin_root))
    monkeypatch.setattr(_managed_temp.tempfile, "gettempdir", lambda: str(temp_root))
    target = _managed_temp.create_managed_temp("hook-test")
    command = _atk_command(target)

    monkeypatch.setattr(subject.shutil, "which", lambda name: str(other_launcher) if name == "atk" else None)
    assert subject.main(_payload(command)) == 0

    plugin_launcher = plugin_root / "bin" / ("atk.cmd" if os.name == "nt" else "atk")
    monkeypatch.setattr(subject.shutil, "which", lambda name: str(plugin_launcher) if name == "atk" else None)
    for rejected in (
        f"{command} extra",
        f"{command} && true",
        f"env X=1 {command}",
        _atk_command(pathlib.Path("relative")),
        _atk_command(temp_root / "unmanaged"),
    ):
        assert subject.main(_payload(rejected)) == 0
    assert not capsys.readouterr().out


def test_entrypoint_subprocess_allows_valid_cleanup_without_deleting(tmp_path: pathlib.Path) -> None:
    """共通入口へ実stdinを渡してallow JSONと非削除を確認する。"""
    plugin_root = pathlib.Path(__file__).resolve().parent.parent
    env = os.environ.copy()
    if os.name == "nt":
        env["TEMP"] = str(tmp_path)
        env["TMP"] = str(tmp_path)
        env["LOCALAPPDATA"] = str(tmp_path / "local-app-data")
    else:
        env["TMPDIR"] = str(tmp_path)
        env["XDG_STATE_HOME"] = str(tmp_path / "state")
    env["PLUGIN_ROOT"] = str(plugin_root)
    created = subprocess.run(
        [sys.executable, str(_HELPER), "create", "--prefix", "hook-subprocess"],
        capture_output=True,
        text=True,
        check=True,
        env=env,
    )
    target = pathlib.Path(created.stdout.strip())
    result = subprocess.run(
        [sys.executable, str(_ENTRYPOINT), "permissionrequest_codex"],
        input=_payload(_command(plugin_root, target)),
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    cleanup = subprocess.run(
        [sys.executable, str(_HELPER), "cleanup", "--path", str(target)],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert result.returncode == 0
    assert json.loads(result.stdout)["hookSpecificOutput"]["decision"] == {"behavior": "allow"}
    assert cleanup.returncode == 0
    assert not result.stderr

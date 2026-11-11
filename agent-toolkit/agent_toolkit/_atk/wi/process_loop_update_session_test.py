# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。
"""統括セッション間の更新実行と自己再起動を検証する。"""

import os
import pathlib
import shutil
import subprocess
import sys
from typing import Any

import pytest

from agent_toolkit import atk  # noqa: E402  # pylint: disable=wrong-import-position
from agent_toolkit._atk.wi import process_loop_update as _pl_update
from agent_toolkit._atk.wi import process_loop_watch as _pl_watch
from agent_toolkit._atk.wi import readiness as _wi_readiness
from agent_toolkit._testing.process_loop_support import fake_run_with_remote_url, raise_system_exit_0
from agent_toolkit._testing.process_loop_update_support import _command_was_called
from agent_toolkit._testing.process_loop_update_support import (
    _process_loop_commands_isolated as _process_loop_commands_isolated,
)
from agent_toolkit._testing.process_loop_update_support import _registered_update_boundary as _registered_update_boundary
from agent_toolkit._testing.process_loop_update_support import _resolve_process_loop_commands as _resolve_process_loop_commands
from agent_toolkit.atk_test import _setup_notes  # noqa: E402  # pylint: disable=wrong-import-position


@pytest.mark.usefixtures("process_loop_commands_isolated")
class TestProcessLoopUpdateAndRestart:
    """1反復後のupdate-dotfiles実行と自己再起動の挙動を検証する。"""

    def test_update_and_execv_called_by_default(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """`--no-update`未指定で開始前更新とclaude後の`os.execv`再起動が行われること。"""
        myrepo = tmp_path / "repo"
        myrepo.mkdir()
        _setup_notes(tmp_path)
        subprocess_calls: list[list[str]] = []
        base_fake_run = fake_run_with_remote_url(myrepo, [], 0)

        def fake_run(cmd: list[str], *_args: object, **kwargs: object) -> subprocess.CompletedProcess[Any]:
            subprocess_calls.append(list(cmd))
            return base_fake_run(cmd, *_args, **kwargs)

        monkeypatch.setattr(subprocess, "run", fake_run)
        monkeypatch.setattr(
            _wi_readiness,
            "count_pending_entries",
            lambda *_a, **_kw: 1,
        )
        execv_calls: list[tuple[str, list[str]]] = []

        def fake_execv(path: str, argv: list[str]) -> None:
            execv_calls.append((path, list(argv)))
            raise SystemExit(0)

        monkeypatch.setattr(os, "execv", fake_execv)
        with pytest.raises(SystemExit):
            atk.main(
                ["wi", "process-loop"],
                home=tmp_path,
            )
        assert execv_calls
        assert execv_calls[0][0] == "/resolved/uv"
        assert pathlib.Path(execv_calls[0][1][0]).name == "uv"
        expected_script = pathlib.Path(sys.argv[0]).resolve()
        assert execv_calls[0][1][1:9] == [
            "run",
            "--no-config",
            "--no-project",
            "--python",
            "3.15",
            "python",
            str(expected_script.parent.parent / "scripts" / "launch.py"),
            str(expected_script),
        ]
        assert _command_was_called(subprocess_calls, "update-dotfiles")
        captured = capsys.readouterr()
        assert "process-loopを再起動します。" in captured.out
        # テスト実行環境（非TTY）ではコンソールタイトル制御文字を一切出力しないこと。
        assert "\033]2;" not in captured.out
        assert "\033]2;" not in captured.err

    def test_update_dotfiles_receives_stripped_env(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """セッション終了後の`update-dotfiles`起動へ、仮想環境を除去した環境を渡すこと。

        `update-dotfiles`は`chezmoi apply`を経て対象リポジトリのuvベースのパッケージ操作へ至るため、
        起動元ツールのエフェメラル仮想環境を引き継がせない。
        """
        myrepo = tmp_path / "repo"
        myrepo.mkdir()
        _setup_notes(tmp_path)
        venv_root = "/home/user/.cache/uv/environments-v2/atk-0123456789abcdef"
        monkeypatch.setenv("VIRTUAL_ENV", venv_root)
        monkeypatch.setenv("PATH", os.pathsep.join((f"{venv_root}/bin", "/usr/bin")))
        update_envs: list[Any] = []
        base_fake_run = fake_run_with_remote_url(myrepo, [], 0)

        def fake_run(cmd: list[str], *_args: object, **kwargs: object) -> subprocess.CompletedProcess[Any]:
            if pathlib.Path(cmd[0]).stem.lower() == "update-dotfiles":
                update_envs.append(kwargs.get("env"))
            return base_fake_run(cmd, *_args, **kwargs)

        monkeypatch.setattr(subprocess, "run", fake_run)
        monkeypatch.setattr(_wi_readiness, "count_pending_entries", lambda *_a, **_kw: 1)
        monkeypatch.setattr(os, "execv", raise_system_exit_0)

        with pytest.raises(SystemExit):
            atk.main(["wi", "process-loop"], home=tmp_path)

        assert len(update_envs) == 1
        for child_env in update_envs:
            assert child_env is not None
            assert "VIRTUAL_ENV" not in child_env
            assert child_env["PATH"] == "/usr/bin"

    def test_wait_loop_update_dotfiles_receives_stripped_env(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """待機ループ復帰時の`update-dotfiles`起動へも、仮想環境を除去した環境を渡すこと。"""
        venv_root = "/home/user/.cache/uv/environments-v2/atk-0123456789abcdef"
        monkeypatch.setenv("VIRTUAL_ENV", venv_root)
        monkeypatch.setenv("PATH", os.pathsep.join((f"{venv_root}/bin", "/usr/bin")))
        update_envs: list[Any] = []

        def fake_run(cmd: list[str], *_a: object, **kwargs: object) -> subprocess.CompletedProcess[Any]:
            if pathlib.Path(cmd[0]).stem.lower() == "update-dotfiles":
                update_envs.append(kwargs.get("env"))
            stdout = "1\n" if "rev-list" in cmd else ""
            return subprocess.CompletedProcess(cmd, 0, stdout, "")

        monkeypatch.setattr(subprocess, "run", fake_run)
        monkeypatch.setattr(_pl_update, "code_hash", lambda _d: "same-hash")  # 再起動へ進ませない
        _pl_update.check_and_restart_on_update(tmp_path, "same-hash", ["argv0"])  # pylint: disable=protected-access  # noqa: SLF001

        assert len(update_envs) == 1
        assert update_envs[0] is not None
        assert "VIRTUAL_ENV" not in update_envs[0]
        assert update_envs[0]["PATH"] == "/usr/bin"

    def test_no_update_skips_restart(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """`--no-update`指定時にupdate-dotfilesと`os.execv`のいずれも呼ばれないこと。"""
        myrepo = tmp_path / "repo"
        myrepo.mkdir()
        _setup_notes(tmp_path)
        counts = iter([1, 1, 0])
        subprocess_calls: list[list[str]] = []
        base_fake_run = fake_run_with_remote_url(myrepo, [], 0)

        def fake_run(cmd: list[str], *_args: object, **kwargs: object) -> subprocess.CompletedProcess[Any]:
            subprocess_calls.append(list(cmd))
            return base_fake_run(cmd, *_args, **kwargs)

        monkeypatch.setattr(subprocess, "run", fake_run)
        monkeypatch.setattr(
            _wi_readiness,
            "count_pending_entries",
            lambda *_a, **_kw: next(counts),
        )

        def fake_wait(*_a: object, **_kw: object) -> None:
            raise KeyboardInterrupt

        monkeypatch.setattr(_pl_watch, "wait_for_changes", fake_wait)
        execv_calls: list[tuple[str, list[str]]] = []
        monkeypatch.setattr(
            os,
            "execv",
            lambda p, a: execv_calls.append((p, list(a))),
        )
        with pytest.raises(SystemExit):
            atk.main(
                ["wi", "process-loop", "--no-update"],
                home=tmp_path,
            )
        assert not execv_calls
        assert not _command_was_called(subprocess_calls, "update-dotfiles")

    def test_missing_update_command_reports_error_and_continues_loop(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """update-dotfilesを解決できない場合も次反復へ進み、待機を継続すること。"""
        myrepo = tmp_path / "repo"
        myrepo.mkdir()
        _setup_notes(tmp_path)
        counts = iter([1, 0])
        monkeypatch.setattr(subprocess, "run", fake_run_with_remote_url(myrepo, [], 0))
        monkeypatch.setattr(_wi_readiness, "count_pending_entries", lambda *_a, **_kw: next(counts))
        monkeypatch.setattr(
            shutil,
            "which",
            lambda command: None if command == "update-dotfiles" else f"/resolved/{command}",
        )

        def fake_wait(*_a: object, **_kw: object) -> None:
            raise KeyboardInterrupt

        monkeypatch.setattr(_pl_watch, "wait_for_changes", fake_wait)

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "process-loop"], home=tmp_path)

        assert exc_info.value.code == 0
        assert "update-dotfilesコマンドを利用できない" in capsys.readouterr().err

    def test_missing_uv_command_reports_error_and_continues_loop(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """再起動用uvを解決できない場合も次反復へ進み、待機を継続すること。"""
        myrepo = tmp_path / "repo"
        myrepo.mkdir()
        _setup_notes(tmp_path)
        counts = iter([1, 1, 1, 0])
        monkeypatch.setattr(subprocess, "run", fake_run_with_remote_url(myrepo, [], 0))
        monkeypatch.setattr(_wi_readiness, "count_pending_entries", lambda *_a, **_kw: next(counts))
        monkeypatch.setattr(
            shutil,
            "which",
            lambda command: None if command == "uv" else f"/resolved/{command}",
        )

        def fake_wait(*_a: object, **_kw: object) -> None:
            raise KeyboardInterrupt

        monkeypatch.setattr(_pl_watch, "wait_for_changes", fake_wait)
        monkeypatch.setattr(os, "execv", lambda *_a, **_kw: pytest.fail("uv未解決時はexecvを呼ばないこと"))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "process-loop"], home=tmp_path)

        assert exc_info.value.code == 0
        assert "uvコマンドを利用できない" in capsys.readouterr().err

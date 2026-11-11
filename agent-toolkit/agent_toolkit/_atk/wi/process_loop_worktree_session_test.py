# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。
"""統括開始前の同期・更新とworktreeオプションの受け渡しを検証する。"""

import contextlib
import pathlib
import subprocess
from collections.abc import Generator
from typing import Any, NoReturn

import pytest

from agent_toolkit import atk  # noqa: E402  # pylint: disable=wrong-import-position
from agent_toolkit._atk import git_sync as _atk_git_sync
from agent_toolkit._atk.wi import process_loop as _process_loop  # noqa: E402  # pylint: disable=wrong-import-position
from agent_toolkit._atk.wi import process_loop_env as _pl_env
from agent_toolkit._atk.wi import process_loop_update as _pl_update
from agent_toolkit._atk.wi import process_loop_watch as _pl_watch
from agent_toolkit._atk.wi import process_loop_worktree as _pl_worktree
from agent_toolkit._atk.wi import sync as _wi_sync
from agent_toolkit._testing.process_loop_worktree_support import (
    _PULL_PRIVATE_NOTES_IMPL,
    _make_remote_repository,
    _run_public_process_loop,
)
from agent_toolkit._testing.process_loop_worktree_support import (
    _prepare_process_loop_environment as _prepare_process_loop_environment,
)
from agent_toolkit._testing.process_loop_worktree_support import (
    _process_loop_commands_isolated as _process_loop_commands_isolated,
)
from agent_toolkit.atk_test import _setup_notes  # noqa: E402  # pylint: disable=wrong-import-position


@pytest.mark.usefixtures("process_loop_commands_isolated")
class TestProcessLoopSessionPreparation:
    """ready項目の処理前同期と失敗時のfail-closed動作を検証する。"""

    def test_private_notes_pull_runs_under_repo_lock(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """private-notesのpullが同一リポジトリのlock保持中だけ実行されること。"""
        events: list[str] = []

        @contextlib.contextmanager
        def fake_lock(path: pathlib.Path) -> Generator[None]:
            assert path == tmp_path
            events.append("lock-enter")
            yield
            events.append("lock-exit")

        monkeypatch.setattr(_wi_sync, "repo_lock", fake_lock)
        monkeypatch.setattr(_wi_sync, "pull", lambda path: events.append(f"pull:{path.name}"))

        assert _PULL_PRIVATE_NOTES_IMPL(tmp_path)
        assert events == ["lock-enter", f"pull:{tmp_path.name}", "lock-exit"]

    def test_pull_private_notes_reports_rebase_in_progress(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """rebase中間状態では例外終了せず、復旧待ちとして偽を返す。"""

        def fail_pull(_path: pathlib.Path) -> NoReturn:
            raise _atk_git_sync.RebaseInProgressError("rebase中")

        monkeypatch.setattr(_wi_sync, "pull", fail_pull)

        assert not _PULL_PRIVATE_NOTES_IMPL(tmp_path)
        stderr = capsys.readouterr().err
        assert "remote同期に失敗（子セッションを起動せず待機します）: rebase中" in stderr
        assert "status`で同期状態を確認し" in stderr.split("\n次の操作: ", 1)[1]

    def test_missing_update_command_does_not_repull_or_start_session(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """更新コマンドが未解決なら再pullへ進まず子セッションも起動しないこと。"""
        monkeypatch.setattr(_pl_env, "resolve_executable", lambda _name: None)
        monkeypatch.setattr(
            _pl_watch,
            "pull_private_notes",
            lambda _plugin_root: pytest.fail("更新コマンド未解決では再pullしないこと"),
        )

        assert _pl_update.update_before_session(  # pylint: disable=protected-access  # noqa: SLF001
            tmp_path,
            None,
            None,
            ["atk"],
            {},
        ) == (False, False)

    def test_update_failure_still_starts_session_and_reports_failure(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """更新が非0で終了しても再pullへ進み、子セッションを起動できる状態を返すこと。"""
        monkeypatch.setattr(_pl_env, "resolve_executable", lambda _name: "update-dotfiles")
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda cmd, **_kwargs: subprocess.CompletedProcess(cmd, 1),
        )
        monkeypatch.setattr(_pl_watch, "pull_private_notes", lambda _private_notes: True)

        assert _pl_update.update_before_session(  # pylint: disable=protected-access  # noqa: SLF001
            tmp_path,
            None,
            None,
            ["atk"],
            {},
        ) == (True, False)
        assert "exit code 1" in capsys.readouterr().err

    def test_registered_start_updates_before_worktree_and_session(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """登録全体の開始前更新がworktree準備と統括起動に先行する。"""
        local = _make_remote_repository(tmp_path, "target")
        events: list[str] = []
        prepare = _pl_worktree.prepare_session_target

        def update(*_args: Any, **_kwargs: Any) -> tuple[bool, bool]:
            events.append("update")
            return True, True

        def preparing(*args: Any, **kwargs: Any) -> Any:
            events.append("prepare")
            return prepare(*args, **kwargs)

        monkeypatch.setattr(_pl_update, "update_before_session", update)
        monkeypatch.setattr(_pl_worktree, "prepare_session_target", preparing)
        sessions, _calls = _run_public_process_loop(monkeypatch, tmp_path, local, ["--worktree=custom"], update_flags=())
        assert events == ["update", "prepare"] and len(sessions) == 1

    @pytest.mark.parametrize(
        ("relative_path", "restarts"),
        [
            ("agent_toolkit/runtime.py", True),
            ("scripts/launch.py", True),
            ("agent_toolkit/runtime_test.py", False),
            ("agent_toolkit/__pycache__/runtime.py", False),
        ],
    )
    def test_before_session_update_detects_real_runtime_files(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, relative_path: str, restarts: bool
    ) -> None:
        """開始前更新は実走査対象の変更だけで再起動する。"""
        plugin_root = tmp_path / "dotfiles" / "agent-toolkit"
        package = plugin_root / "agent_toolkit"
        scripts = plugin_root / "scripts"
        package.mkdir(parents=True)
        scripts.mkdir()
        (package / "runtime.py").write_text("before = 1\n", encoding="utf-8")
        (package / "runtime_test.py").write_text("before = 1\n", encoding="utf-8")
        (scripts / "launch.py").write_text("before = 1\n", encoding="utf-8")
        startup_hash = _pl_update.code_hash(plugin_root)
        monkeypatch.setattr(_pl_env, "resolve_executable", lambda _name: "update-dotfiles")
        monkeypatch.setattr(_pl_watch, "pull_private_notes", lambda _private_notes: True)
        restart_spec = tmp_path / "restart-spec"
        monkeypatch.setenv("AGENT_TOOLKIT_RESTART_SPEC", str(restart_spec))

        def fake_run(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
            path = plugin_root / relative_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("after = 2\n", encoding="utf-8")
            return subprocess.CompletedProcess(cmd, 0, "", "")

        monkeypatch.setattr(subprocess, "run", fake_run)
        if restarts:
            with pytest.raises(SystemExit) as restart:
                _pl_update.update_before_session(
                    tmp_path, tmp_path / "dotfiles", startup_hash, ["atk", "wi", "process-loop"], {}
                )
            expected = _pl_update._RESTART_EXIT_CODE  # pylint: disable=protected-access  # noqa: SLF001
            assert restart.value.code == expected
        else:
            result = _pl_update.update_before_session(
                tmp_path, tmp_path / "dotfiles", startup_hash, ["atk", "wi", "process-loop"], {}
            )
            assert result == (True, True)
            assert not restart_spec.exists()

    def test_registered_updated_marker_skips_duplicate_update(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """開始前更新済みの印を引き継いだ反復は同じ更新を繰り返さない。"""
        local = _make_remote_repository(tmp_path, "target")

        def unexpected(*_args: Any, **_kwargs: Any) -> Any:
            pytest.fail("更新済みの反復でupdate_before_sessionを呼び出した")

        monkeypatch.setattr(_pl_update, "update_before_session", unexpected)
        sessions, _calls = _run_public_process_loop(
            monkeypatch, tmp_path, local, [], update_flags=("--internal-dotfiles-updated",)
        )
        assert len(sessions) == 1

    def test_registered_preparation_failure_returns_to_wait(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """準備失敗時は統括を起動せず、キューと対象作業を保持して待機する。"""
        local = _make_remote_repository(tmp_path, "target")
        monkeypatch.setattr(_pl_worktree, "prepare_session_target", lambda *_args, **_kwargs: None)
        sessions, _calls = _run_public_process_loop(monkeypatch, tmp_path, local, ["--worktree"])
        assert not sessions
        assert (tmp_path / "private-notes/2026/10/worktree.md").is_file()


@pytest.mark.usefixtures("process_loop_commands_isolated")
def test_process_loop_worktree_option_reaches_public_handler(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """公開CLIのworktree指定がprocess-loopハンドラへ伝わること。"""
    _setup_notes(tmp_path)
    received: list[str | None] = []

    def fake_process_loop(args: Any, _private_notes: pathlib.Path) -> NoReturn:
        received.append(args.worktree)
        raise SystemExit(0)

    monkeypatch.setattr(_process_loop, "cmd_process_loop", fake_process_loop)
    for extra_argv, expected in (([], None), (["--worktree"], "process-loop"), (["--worktree=custom"], "custom")):
        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "process-loop", *extra_argv], home=tmp_path)
        assert exc_info.value.code == 0
        assert received[-1] == expected


@pytest.mark.usefixtures("process_loop_commands_isolated")
@pytest.mark.parametrize("value", ["/tmp/worktree", "name/../other", "", ".hidden", "-leading"])
def test_process_loop_rejects_invalid_worktree_name(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    value: str,
) -> None:
    """公開CLIが配置先を逸脱するworktree名を拒否すること。"""
    called = False

    def fake_process_loop(_args: Any, _private_notes: pathlib.Path) -> NoReturn:
        nonlocal called
        called = True
        raise SystemExit(0)

    monkeypatch.setattr(_process_loop, "cmd_process_loop", fake_process_loop)
    with pytest.raises(SystemExit) as exc_info:
        atk.main(["wi", "process-loop", f"--worktree={value}"], home=tmp_path)
    assert exc_info.value.code == 2
    assert not called


@pytest.mark.usefixtures("process_loop_commands_isolated")
class TestProcessLoopWorktreeOption:
    """対象worktree名と、常駐cwdで起動する統括への受渡しを確認する。"""

    @pytest.mark.parametrize(("flags", "name"), [(["--worktree"], "process-loop"), (["--worktree=custom"], "custom")])
    def test_registered_worktree_option(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, flags: list[str], name: str
    ) -> None:
        local = _make_remote_repository(tmp_path, "target")
        sessions, _calls = _run_public_process_loop(monkeypatch, tmp_path, local, flags)
        assert sessions[0]["worktree"] == local / ".claude/worktrees" / name
        assert sessions[0]["cwd"] == pathlib.Path.cwd()
        assert sessions[0]["target"]["target_repo"] == "github.com/example/repo"

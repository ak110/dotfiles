"""atkの契約を検証する。"""

from __future__ import annotations

import contextlib
import datetime
import json
import os
import pathlib
import subprocess
import sys
import time
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

import pytest

from agent_toolkit import atk
from agent_toolkit._atk import config as _config
from agent_toolkit._atk import git_sync as _git_sync
from agent_toolkit._atk import managed_temp as _managed_temp
from agent_toolkit._atk.managed_temp import auto_sweep as _auto_sweep
from agent_toolkit._atk.managed_temp import sweep as _sweep
from agent_toolkit._atk.wi import listing as _wi_listing
from agent_toolkit._atk.wi import sync as _wi_sync
from agent_toolkit._atk.wi import web_input as _wi_web_input
from agent_toolkit._atk.wi.mutations import targets as _mutation_targets
from agent_toolkit._common import codex_models, inherited_venv
from agent_toolkit._testing import git_repository, review_bodies, wi_entry_files
from agent_toolkit._testing import wi_bodies as _wi_bodies
from agent_toolkit._testing.atk_support import (
    _ATK_PATH,
    _CONCURRENT_PUSH_SCRIPT,
    _DRY_RUN_SUCCESS,
    _EDIT_TARGET,
    _FIXED_DT,
    _FIXED_TIMESTAMP,
    _NON_SAVING_ENTRIES,
    _PROJECT_ROOT,
    _SYNC_MUTATION_ENTRIES,
    _child_config_env,
    _GitCall,
    _isolate_agent_and_managed_temp_environment,
    _isolated_cli_environ,
    _make_subprocess_fake,
    _prepare_non_saving_operation,
    _prepare_sync_mutation,
    _setup_notes,
    _setup_notes_with_pending_commit,
    _write_awi_file,
    _write_uwi_file,
)

__all__ = [
    "_FIXED_DT",
    "_FIXED_TIMESTAMP",
    "_GitCall",
    "_isolate_agent_and_managed_temp_environment",
    "_make_subprocess_fake",
    "_setup_notes",
    "_setup_notes_with_pending_commit",
    "_write_awi_file",
    "_write_uwi_file",
]


@pytest.mark.usefixtures("_isolate_agent_and_managed_temp_environment")
def test_worker_retains_deletion_diagnostics_after_a_noop(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    failure = ((tmp_path / "failed-area", "persistent deletion failure"),)
    results = iter((_sweep.SweepResult([], failure), _sweep.SweepResult([], ())))
    monkeypatch.setattr(_sweep, "sweep_managed_temp", lambda **_kwargs: next(results))
    monkeypatch.setenv("AGENT_TOOLKIT_DELEGATED_SESSION", "1")

    _auto_sweep._run_worker()  # pylint: disable=protected-access
    log = _sweep.paths.state_root() / ".sweep/last-auto-sweep.log"
    report = log.read_text(encoding="utf-8")
    assert "persistent deletion failure" in report and "次の操作:" in report
    _auto_sweep._run_worker()  # pylint: disable=protected-access
    assert log.read_text(encoding="utf-8") == report


def test_entry_restores_launch_environment_for_children(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PYTHONPATH", "/plugin-root")
    monkeypatch.setenv(inherited_venv.LAUNCH_RESTORE_KEY, json.dumps({"PYTHONPATH": [None, "/plugin-root"]}))
    atk.main(["info"])
    assert "PYTHONPATH" not in os.environ
    assert inherited_venv.LAUNCH_RESTORE_KEY not in os.environ


def test_info_reports_current_execution_context(capsys: pytest.CaptureFixture[str]) -> None:
    atk.main(["info"])

    output = capsys.readouterr().out
    assert f"作業ディレクトリ: {pathlib.Path.cwd()}" in output
    assert "atk実装:" not in output
    assert f"plugin root: {_PROJECT_ROOT}" in output
    assert "plugin version (plugin.json):" in output


@pytest.mark.parametrize(
    "argv",
    [
        ["wi", "list", "--skip-pull"],
        ["wi", "show", "--all", "--skip-pull"],
        ["wi", "grep", ".", "--skip-pull"],
        ["config", "show"],
    ],
)
def test_cli_exits_quietly_when_stdout_pipe_is_closed_early(
    tmp_path: pathlib.Path,
    argv: list[str],
    host_environ: Callable[[], dict[str, str]],
) -> None:
    """公開された出力処理は読取側の早期クローズをTracebackなしのexit 1として処理する。"""
    notes = _setup_notes(tmp_path)
    _write_awi_file(notes, "awi.md", body="searchable")
    with contextlib.ExitStack() as descriptors, contextlib.ExitStack() as reader_owner:
        read_fd, write_fd = os.pipe()
        descriptors.callback(os.close, write_fd)
        reader_owner.callback(os.close, read_fd)
        reader_owner.close()
        env = _isolated_cli_environ(host_environ, tmp_path)
        env["AGENT_TOOLKIT_PRIVATE_NOTES"] = str(notes)
        env["XDG_CONFIG_HOME"] = str(tmp_path / "config")
        env["LOCALAPPDATA"] = str(tmp_path / "config")
        env.update(_child_config_env())
        # `codex`が起動されると印のファイルを書く偽の実行ファイルをPATHの先頭へ置き、起動の有無を観測する。
        fake_bin = tmp_path / "fake-bin"
        fake_bin.mkdir()
        marker = tmp_path / "codex-started"
        fake_codex = fake_bin / "codex"
        fake_codex.write_text(f"#!/bin/sh\ntouch '{marker}'\nexit 1\n", encoding="utf-8")
        fake_codex.chmod(0o755)
        env["PATH"] = os.pathsep.join((str(fake_bin), env.get("PATH", "")))

        with subprocess.Popen(  # noqa: S603
            [sys.executable, str(_ATK_PATH), *argv],
            stdout=write_fd,
            stderr=subprocess.PIPE,
            env=env,
            cwd=tmp_path,
            text=True,
        ) as process:
            stderr = process.communicate()[1]

            assert process.returncode == 1
        assert "Traceback" not in stderr
        assert "Exception ignored on flushing sys.stdout" not in stderr
        assert not marker.exists()


def test_child_config_env_derives_values_from_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(
        _config._MUTABLE_KEY_DEFAULTS,  # pylint: disable=protected-access  # noqa: SLF001
        "future_stage_model",
        "claude:opus/medium",
    )
    env = _child_config_env()
    assert set(env) == {
        f"AGENT_TOOLKIT_CONFIG_{key.upper()}"
        for key in _config._MUTABLE_KEY_DEFAULTS  # pylint: disable=protected-access  # noqa: SLF001
    }
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    for key in _config._MUTABLE_KEY_DEFAULTS:  # pylint: disable=protected-access  # noqa: SLF001
        _config.resolve_mutable_setting(key)
    candidates: list[tuple[str, str, str]] = []
    for name, value in env.items():
        if name.endswith("_MODEL"):
            candidates.extend(_config.parse_stage_model_candidates(value))
    assert not codex_models.needs_catalog(candidates)


@pytest.mark.parametrize("entry", _SYNC_MUTATION_ENTRIES)
def test_local_mutations_do_not_report_existing_pending_commit(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    entry: str,
) -> None:
    notes = _setup_notes_with_pending_commit(tmp_path)
    monkeypatch.setattr(_wi_sync, "ensure_environment", lambda _home: notes)
    monkeypatch.setattr(_wi_listing, "cmd_list", lambda *_args: None)

    monkeypatch.setattr(_wi_sync, "push_pending_commits", lambda _notes: 0)
    argv = _prepare_sync_mutation(entry, monkeypatch)

    with pytest.raises(SystemExit) as exc_info:
        atk.main(argv, home=tmp_path)
    monkeypatch.undo()
    assert exc_info.value.code == 0
    assert "未pushのcommit" not in capsys.readouterr().err

    monkeypatch.setattr(_wi_sync, "ensure_environment", lambda _home: notes)
    monkeypatch.setattr(_wi_listing, "cmd_list", lambda *_args: None)
    with pytest.raises(SystemExit) as exc_info:
        atk.main(["wi", "list", "--skip-pull"], home=tmp_path)
    assert exc_info.value.code == 0
    assert "未pushのcommit" not in capsys.readouterr().err


@pytest.mark.parametrize("entry", _NON_SAVING_ENTRIES)
def test_main_does_not_report_pending_commit_for_operations_without_commit(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    entry: str,
) -> None:
    notes = _setup_notes_with_pending_commit(tmp_path, pending=False)
    _write_awi_file(notes, _EDIT_TARGET, body=_wi_bodies.AGENT_AWI_BODY, source="session-review")
    git_repository.commit_all(notes, "awi")
    git_repository.run_git(notes, "push")
    (notes / "pending.txt").write_text("pending\n", encoding="utf-8")
    pending = git_repository.commit_all(notes, "pending")
    monkeypatch.setattr(_wi_sync, "ensure_environment", lambda _home: notes)
    argv = _prepare_non_saving_operation(entry, notes, tmp_path, monkeypatch)

    with pytest.raises(SystemExit) as exc_info:
        atk.main(argv, home=tmp_path, now=datetime.datetime(2026, 7, 15, 9, 0, 0))

    output = capsys.readouterr()
    assert exc_info.value.code == 0, output.err
    assert "未pushのcommit" not in output.err
    if entry == "add --dry-run":
        assert _DRY_RUN_SUCCESS in output.out
    assert git_repository.git_output(notes, "rev-parse", "HEAD") == pending
    assert git_repository.git_output(notes, "rev-list", "--count", "@{u}..HEAD") == "1"


@pytest.mark.parametrize("entry", _SYNC_MUTATION_ENTRIES)
def test_main_does_not_report_commit_pushed_by_concurrent_process(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    entry: str,
) -> None:
    """別プロセスがロック内でcommitしてからpushする間に終えた同期対象操作は、未pushを通知しない。

    commit後・push前の一時状態を読むと、同期が完了する途中の状態を同期未達として終了コード3で報告する。
    """
    notes = _setup_notes_with_pending_commit(tmp_path, pending=False)
    monkeypatch.setattr(_wi_sync, "ensure_environment", lambda _home: notes)
    argv = _prepare_sync_mutation(entry, monkeypatch)
    script = tmp_path / "concurrent_push.py"
    script.write_text(_CONCURRENT_PUSH_SCRIPT, encoding="utf-8")
    ready = tmp_path / "ready"
    release = tmp_path / "push.release"
    repo_lock = _git_sync.repo_lock

    def release_on_lock(*args: Any, **kwargs: Any) -> Any:
        assert ready.exists()
        release.touch()
        return repo_lock(*args, **kwargs)

    monkeypatch.setattr(_git_sync, "repo_lock", release_on_lock)
    environ = os.environ.copy()
    environ["PYTHONPATH"] = os.pathsep.join(filter(None, [str(_PROJECT_ROOT), environ.get("PYTHONPATH")]))
    with subprocess.Popen([sys.executable, str(script), str(notes), str(ready), str(release)], env=environ) as writer:
        deadline = time.monotonic() + 30
        while not ready.exists():
            assert writer.poll() is None, "別プロセスがcommit前に終了した"
            assert time.monotonic() < deadline, "別プロセスのcommitを観測できない"
            time.sleep(0.05)

        with pytest.raises(SystemExit) as exc_info:
            atk.main(argv, home=tmp_path)
        release.touch()
        assert writer.wait(timeout=30) == 0

    assert exc_info.value.code == 0
    assert "未pushのcommit" not in capsys.readouterr().err


class TestMainFailureNextAction:
    """`atk.py:main`が捕捉した例外を失敗行と次の操作の行の2行で出力することを検証する。"""

    @staticmethod
    def _run_wi_commit(
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        error: BaseException,
    ) -> list[str]:
        monkeypatch.setattr(_wi_sync, "ensure_environment", lambda _home: tmp_path)

        def fail(*_args: object) -> None:
            raise error

        monkeypatch.setattr(_mutation_targets, "cmd_commit", fail)
        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "commit"], home=tmp_path, now=_FIXED_DT)
        assert exc_info.value.code == 1
        return capsys.readouterr().err.splitlines()

    @staticmethod
    def _next_action_after_failure(lines: list[str]) -> str:
        """失敗行の直後の行が次の操作の行であることを確かめて返す。"""
        index = next(i for i, line in enumerate(lines) if line.startswith("失敗: "))
        assert lines[index + 1].startswith("次の操作: ")
        return lines[index + 1]

    def test_actionable_error_prints_its_next_action(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        error = _wi_web_input.WebInputError("対象が無い", next_action="`atk wi list`で対象を確認する")
        lines = self._run_wi_commit(tmp_path, monkeypatch, capsys, error)
        assert lines[0] == "失敗: 操作を拒否した: 対象が無い"
        assert self._next_action_after_failure(lines) == "次の操作: `atk wi list`で対象を確認する"

    def test_plain_value_error_names_help_and_bug_report(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        lines = self._run_wi_commit(tmp_path, monkeypatch, capsys, ValueError("想定外の値"))
        next_action = self._next_action_after_failure(lines)
        assert "atk wi commit --help" in next_action

    def test_rebase_in_progress_error_is_reported_without_traceback(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """wiの処理で発生した`RebaseInProgressError`は失敗行と競合解消の次の操作で出力する。"""
        error = _git_sync.RebaseInProgressError("rebase中", tmp_path)
        lines = self._run_wi_commit(tmp_path, monkeypatch, capsys, error)
        assert str(tmp_path) in self._next_action_after_failure(lines)
        assert "rebase --continue" in self._next_action_after_failure(lines)

    def test_wi_edit_rejects_rebase_in_progress_before_change(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        notes = tmp_path / "notes"
        git_repository.init_repository(notes)
        original = "---\nstate: inbox\ntype: awi\ntarget_repo: github.com/example/repo\n---\n\n本文\n"
        entry = wi_entry_files.entry_path(notes, "entry.md")
        entry.write_text(original, encoding="utf-8")
        git_repository.run_git(notes, "add", "-A")
        git_repository.run_git(notes, "commit", "-m", "setup")
        rebase_dir = git_repository.run_git(notes, "rev-parse", "--git-path", "rebase-merge").stdout.strip()
        (notes / rebase_dir).mkdir(parents=True)
        monkeypatch.setattr(_wi_sync, "ensure_environment", lambda _home: notes)
        body = tmp_path / "body.md"
        body.write_text(original.replace("本文", "変更後の本文"), encoding="utf-8")

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "edit", "entry.md", "--body-file", str(body)], home=tmp_path, now=_FIXED_DT)

        assert exc_info.value.code == 1
        lines = capsys.readouterr().err.splitlines()
        assert "rebase中" in next(line for line in lines if line.startswith("失敗: "))
        assert str(notes) in self._next_action_after_failure(lines)
        assert "rebase --continue" in self._next_action_after_failure(lines)
        assert entry.read_text(encoding="utf-8") == original

    def test_unreported_git_failure_names_status_check(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """同期基盤が報告していないGit操作の失敗は、`git -C <private-notes> status`での確認を案内する。"""
        error = subprocess.CalledProcessError(1, ["git", "commit"])
        lines = self._run_wi_commit(tmp_path, monkeypatch, capsys, error)
        assert f"`git -C {tmp_path.resolve()} status`" in self._next_action_after_failure(lines)

    def test_reported_git_failure_is_not_repeated(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """同期基盤が原因と次の操作を出力済みのGit操作の失敗へ、汎用の失敗行を重ねない。"""
        error = subprocess.CalledProcessError(1, ["git", "push"])
        _git_sync.mark_reported(error)
        lines = self._run_wi_commit(tmp_path, monkeypatch, capsys, error)
        assert not any(line.startswith("失敗: ") for line in lines)


@pytest.mark.usefixtures("_isolate_agent_and_managed_temp_environment")
def test_wi_preview_keeps_pressure_cleanup_for_the_next_write(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    notes = _setup_notes_with_pending_commit(tmp_path, pending=False)
    monkeypatch.setenv(_sweep.MIN_FREE_RATIO_ENV, "0.10")
    idle = _managed_temp.temp_root_path() / "idle-old"
    idle.mkdir(parents=True)
    now = datetime.datetime.now()
    _sweep._save_sweep_time(_sweep.paths.state_root(), _sweep._now_ns(now))  # pylint: disable=protected-access
    old = (now - datetime.timedelta(days=8)).timestamp()
    os.utime(idle, (old, old))
    monkeypatch.setattr(
        _sweep,
        "_disk_usage",
        lambda _path: SimpleNamespace(total=100, free=5 if idle.exists() else 15),
    )
    preview = _prepare_non_saving_operation("add --dry-run", notes, tmp_path, monkeypatch)
    with pytest.raises(SystemExit) as preview_result:
        atk.main(preview, home=tmp_path, now=now)
    assert preview_result.value.code == 0 and idle.is_dir()
    assert _DRY_RUN_SUCCESS in capsys.readouterr().out

    table = tmp_path / "review.tsv"
    table.touch()
    for query in (
        ["plans", "list"],
        ["review-table", "show", str(table)],
        ["review-table", "validate", str(table), "--allow-unanswered"],
        ["config", "repos", "list"],
    ):
        with pytest.raises(SystemExit) as observed:
            atk.main(query, home=tmp_path, now=now)
        assert observed.value.code == 0 and idle.is_dir(), query
    capsys.readouterr()

    scheduled: list[bool] = []
    monkeypatch.setattr(_auto_sweep, "_start_worker", lambda: scheduled.append(True))
    monkeypatch.setattr(_wi_sync, "ensure_environment", lambda _home: notes)
    write = _prepare_sync_mutation("wi start-processing", monkeypatch)
    with pytest.raises(SystemExit) as write_result:
        atk.main(write, home=tmp_path, now=now)
    assert write_result.value.code == 0 and idle.exists() and scheduled == [True]
    assert (notes / "started.txt").is_file()
    assert "空きが下限" in capsys.readouterr().err


def test_report_space_pressure_writes_top_reclaimed_to_stderr(capsys: pytest.CaptureFixture[str]) -> None:
    gib = 1024**3
    pressure = _managed_temp.SpacePressure(
        100 * gib, 5 * gib, 12 * gib, 7 * gib, (("pytest-old", 4 * gib), ("big-area", 3 * gib))
    )

    _auto_sweep._report_space_pressure(pressure)  # pylint: disable=protected-access

    output = capsys.readouterr()
    assert output.out == ""
    assert "pytest-old=4.00GiB, big-area=3.00GiB" in output.err
    assert "空き 5.0GiB→12.0GiB" in output.err


def test_report_space_pressure_warns_with_free_and_total_when_still_low(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:

    monkeypatch.delenv(_sweep.MIN_FREE_RATIO_ENV, raising=False)
    gib = 1024**3
    pressure = _managed_temp.SpacePressure(100 * gib, 3 * gib, 3 * gib, 0, ())

    _auto_sweep._report_space_pressure(pressure)  # pylint: disable=protected-access

    output = capsys.readouterr()
    assert output.out == ""
    assert "自動掃引後も空きが不足" in output.err
    assert "回収できる対象が無い" not in output.err
    assert "作成者が終了すると次の起動で回収できる" not in output.err
    assert "空き 3.0GiB / 容量 100.0GiB" in output.err


@pytest.mark.usefixtures("_isolate_agent_and_managed_temp_environment")
@pytest.mark.parametrize("operation", ["respond", "plan-progress"])
def test_first_write_does_not_wait_for_pressure_sweep(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    managed = _sweep.paths.temp_root()
    state = _sweep.paths.state_root()
    now = datetime.datetime.now().astimezone()
    _sweep._save_sweep_time(state, _sweep._now_ns(now))  # pylint: disable=protected-access
    idle = managed / "idle-old"
    active = managed / "active-old"
    for area in (idle, active):
        area.mkdir()
    (active / _sweep.OWNER_FILE).write_text(_sweep.owner_token(), encoding="utf-8")
    old = (now - datetime.timedelta(days=8)).timestamp()
    os.utime(idle, (old, old))
    os.utime(active, (old, old))

    if operation == "respond":
        target = tmp_path / "review.exec-review.tsv"
        target.write_text(
            "\t".join(json.dumps(cell) for cell in ("1", "exec-review", "位置", "指摘", "", "", "")) + "\n",
            encoding="utf-8",
        )
        response = tmp_path / "response.md"
        response.write_text(
            review_bodies.response_body("修正した"),
            encoding="utf-8",
        )
        args = ["review-table", "respond", str(target), "--row-id=1", f"--response-file={response}"]
    else:
        target = tmp_path / "plan.md"
        target.write_text("## 進捗ログ\n\n| 日時 | 完了した工程 | 結果・特記事項 |\n| --- | --- | --- |\n", encoding="utf-8")
        args = ["run-script", "plan-progress", "--", str(target), "--completed-step=試験", "--result=修正した"]

    delay = tmp_path / "sitecustomize.py"
    delay.write_text(
        """
import time
from pathlib import Path
from types import SimpleNamespace
from agent_toolkit._atk.managed_temp import sweep
sweep._disk_usage = lambda _path: SimpleNamespace(total=1000, free=80)
reclaim = sweep._reclaim_space
def slow_reclaim(*args, **kwargs):
    time.sleep(5)
    result = reclaim(*args, **kwargs)
    Path(__file__).with_name("sweep-done").touch()
    return result
sweep._reclaim_space = slow_reclaim
""",
        encoding="utf-8",
    )
    root = pathlib.Path(atk.__file__).resolve().parents[1]
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join((str(tmp_path), str(root))))
    monkeypatch.setenv(_sweep.MIN_FREE_RATIO_ENV, "0.10")
    for name in ("CLAUDE_CODE_SESSION_ID", "CODEX_THREAD_ID", "AGENT_TOOLKIT_OWNER_SESSION"):
        monkeypatch.delenv(name, raising=False)
    started = time.monotonic()
    completed = subprocess.run([sys.executable, "-m", "agent_toolkit.atk", *args], capture_output=True, check=False, timeout=25)
    elapsed = time.monotonic() - started
    print(f"{operation}: {elapsed:.3f}秒")
    done = tmp_path / "sweep-done"
    log = state / ".sweep/last-auto-sweep.log"
    try:
        assert completed.returncode == 0, completed.stderr.decode()
        assert "修正した" in target.read_text(encoding="utf-8")
        assert "空きが下限" in completed.stderr.decode()
        assert not done.exists(), "CLIが掃引完了後に戻った"
    finally:
        deadline = time.monotonic() + 20
        while not log.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
    assert done.exists() and active.is_dir() and not idle.exists()
    report = log.read_text(encoding="utf-8")
    assert "idle-old" in report and "自動掃引後も空きが不足" in report

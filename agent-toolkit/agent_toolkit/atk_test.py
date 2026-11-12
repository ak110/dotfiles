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
from typing import Any

import pytest

from agent_toolkit import atk
from agent_toolkit._atk import config as _config
from agent_toolkit._atk import git_sync as _git_sync
from agent_toolkit._atk import managed_temp as _managed_temp
from agent_toolkit._atk.managed_temp import sweep as _sweep
from agent_toolkit._atk.wi import listing as _wi_listing
from agent_toolkit._atk.wi import sync as _wi_sync
from agent_toolkit._atk.wi import web_input as _wi_web_input
from agent_toolkit._atk.wi.mutations import targets as _mutation_targets
from agent_toolkit._common import codex_models, inherited_venv
from agent_toolkit._common import wait_schedule as _wait_schedule
from agent_toolkit._testing import git_repository, wi_entry_files
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
from agent_toolkit._testing.managed_temp_support import setattr_in_managed_temp_modules

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


def test_persistent_warning_once_per_session(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: pathlib.Path,
) -> None:
    """期限切れの対象を削除できなかった掃引の失敗を、同じ会話で1回だけ公開サブコマンドから警告する。"""
    for key in ("AGENT_TOOLKIT_OWNER_SESSION", "AGENT_TOOLKIT_DELEGATED_SESSION", "CLAUDE_CODE_SESSION_ID", "CODEX_THREAD_ID"):
        monkeypatch.delenv(key, raising=False)
    failed = tmp_path / "failed-area"
    setattr_in_managed_temp_modules(
        monkeypatch,
        "sweep_managed_temp",
        lambda **_kwargs: _managed_temp.SweepResult([], ((failed, "persistent deletion failure"),)),
    )
    monkeypatch.setattr(_wait_schedule, "get_schedule", lambda _request_bucket: "result")

    def expect_warning(expected: bool) -> None:
        with pytest.raises(SystemExit) as raised:
            atk.main(["wait-schedule"], home=tmp_path)
        assert raised.value.code == 0
        output = capsys.readouterr()
        assert json.loads(output.out)["cron"] == "result"
        assert ("managed-tempの期限切れの対象を自動削除できなかった" in output.err) is expected
        if expected:
            assert "persistent deletion failure" in output.err and str(failed) in output.err

    for key in ("CLAUDE_CODE_SESSION_ID", "CODEX_THREAD_ID"):
        monkeypatch.setenv(key, key)
        expect_warning(True)
        expect_warning(False)
        monkeypatch.setenv(key, key + "-other")
        expect_warning(True)
        expect_warning(False)
        monkeypatch.delenv(key)
    expect_warning(True)
    expect_warning(True)
    monkeypatch.setenv("AGENT_TOOLKIT_DELEGATED_SESSION", "1")
    expect_warning(False)
    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_SESSION")
    monkeypatch.setenv("AGENT_TOOLKIT_OWNER_SESSION", "owner-only")
    expect_warning(False)


def test_entry_restores_launch_environment_for_children(monkeypatch: pytest.MonkeyPatch) -> None:
    """`atk.main`は開始時に、起動層が変えた値を呼出元の値へ戻し、`atk commit`や`$EDITOR`などの子へ渡さない。"""
    monkeypatch.setenv("PYTHONPATH", "/plugin-root")
    monkeypatch.setenv(inherited_venv.LAUNCH_RESTORE_KEY, json.dumps({"PYTHONPATH": [None, "/plugin-root"]}))
    atk.main(["info"])
    assert "PYTHONPATH" not in os.environ
    assert inherited_venv.LAUNCH_RESTORE_KEY not in os.environ


def test_info_reports_current_execution_context(capsys: pytest.CaptureFixture[str]) -> None:
    """公開CLIのinfoが実行文脈とplugin版数の所在を返す。"""
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
        env.update(_child_config_env())
        # `codex`が起動されると印のファイルを書く偽の実行ファイルをPATHの先頭へ置き、起動の有無を観測する。
        fake_bin = tmp_path / "fake-bin"
        fake_bin.mkdir()
        marker = tmp_path / "codex-started"
        fake_codex = fake_bin / "codex"
        fake_codex.write_text(f"#!/bin/sh\ntouch '{marker}'\nexit 1\n", encoding="utf-8")
        fake_codex.chmod(0o755)
        env["PATH"] = os.pathsep.join((str(fake_bin), env.get("PATH", "")))
        # Gitの作業ツリー外で起動し、`--target-repo`を省略した場合も対象を限定しない状態にする。
        with subprocess.Popen(  # noqa: S603
            ["uv", "run", "--project", str(_PROJECT_ROOT), "--locked", "--no-default-groups", str(_ATK_PATH), *argv],
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
    """書式の異なる設定キーを加えても、組み立てた全キーの値が設定の検証に合格する。

    テストが値の書式を自前で持つと、段位モデルとengine順以外の書式のキーを加えるたびに検証で拒否される。
    加えるキーは候補列の検証（engine接頭辞付き）の対象になり、キー名から書式を推測する値（`opus/medium`）は拒否される。
    """
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
def test_main_reports_pending_commit_only_for_sync_mutations(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    entry: str,
) -> None:
    """commitか送信を試みた操作は、送信できずに残った未pushを終了コード3で通知する。"""
    notes = _setup_notes_with_pending_commit(tmp_path)
    monkeypatch.setattr(_wi_sync, "ensure_environment", lambda _home: notes)
    monkeypatch.setattr(_wi_listing, "cmd_list", lambda *_args: None)
    # 送信が成立しなかった状態を作る。commitは行われ、リモートへは届かない。
    monkeypatch.setattr(_wi_sync, "push_pending_commits", lambda _notes: 0)
    argv = _prepare_sync_mutation(entry, monkeypatch)

    with pytest.raises(SystemExit) as exc_info:
        atk.main(argv, home=tmp_path)
    monkeypatch.undo()
    assert exc_info.value.code == 3
    output = capsys.readouterr()
    assert "警告: private-notesに未pushのcommitが" in output.err
    # pushの手順は警告行に続く次の操作の行へ置く。
    next_action = next(line for line in output.err.splitlines() if line.startswith("次の操作: "))
    assert f"`git -C {notes.resolve()} status`" in next_action
    assert "atk wi commit" in next_action

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
    """commitも送信も試みない操作は、他の主体が残した未pushを警告にも終了コードにも反映しない。"""
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
        """`atk wi commit`の処理が`error`を送出したときの標準エラーの行を返す。"""
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
        """`WebInputError`は発生源が渡した次の操作を出力する。"""
        error = _wi_web_input.WebInputError("対象が無い", next_action="`atk wi list`で対象を確認する")
        lines = self._run_wi_commit(tmp_path, monkeypatch, capsys, error)
        assert lines[0] == "失敗: 操作を拒否した: 対象が無い"
        assert self._next_action_after_failure(lines) == "次の操作: `atk wi list`で対象を確認する"

    def test_plain_value_error_names_help_and_bug_report(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """共通型でない`ValueError`は受理形式の確認と不具合の報告を案内する。"""
        lines = self._run_wi_commit(tmp_path, monkeypatch, capsys, ValueError("想定外の値"))
        next_action = self._next_action_after_failure(lines)
        assert "`atk wi commit --help`" in next_action
        assert "ユーザーへ報告" in next_action

    def test_rebase_in_progress_error_is_reported_without_traceback(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """wiの処理で発生した`RebaseInProgressError`は失敗行と競合解消の次の操作で出力する。"""
        error = _git_sync.RebaseInProgressError("rebase中")
        lines = self._run_wi_commit(tmp_path, monkeypatch, capsys, error)
        assert "git -c submodule.recurse=false rebase --continue" in self._next_action_after_failure(lines)

    def test_wi_edit_rejects_rebase_in_progress_before_change(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """rebaseの中間状態が残るWI保存リポジトリでは、`atk wi edit`が本文を変える前に拒否し、解消操作を示す。"""
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
        assert "git -c submodule.recurse=false rebase --continue" in self._next_action_after_failure(lines)
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


def test_report_space_pressure_writes_top_reclaimed_to_stderr(capsys: pytest.CaptureFixture[str]) -> None:
    """回収した起動は回収量の上位の対象名を標準エラーへ、標準出力へは何も出さない。"""
    gib = 1024**3
    pressure = _managed_temp.SpacePressure(
        100 * gib, 5 * gib, 12 * gib, 7 * gib, (("pytest-old", 4 * gib), ("big-area", 3 * gib)), None
    )

    atk._report_space_pressure(pressure)  # pylint: disable=protected-access

    output = capsys.readouterr()
    assert output.out == ""
    assert "pytest-old=4.00GiB, big-area=3.00GiB" in output.err
    assert "空き 5.0GiB→12.0GiB" in output.err


def test_report_space_pressure_warns_with_usage_when_still_low(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """回収できず下限を割ったままなら、空きと一時rootの使用量を標準エラーへ出す。"""
    # 試験基盤が子プロセス向けに設定した下限の上書きを外し、容量の10%の下限で判定する。
    monkeypatch.delenv(_sweep.MIN_FREE_RATIO_ENV, raising=False)
    gib = 1024**3
    pressure = _managed_temp.SpacePressure(100 * gib, 3 * gib, 3 * gib, 0, (), 20 * gib)

    atk._report_space_pressure(pressure)  # pylint: disable=protected-access

    output = capsys.readouterr()
    assert output.out == ""
    assert "回収できる対象が無い" in output.err
    assert "空き 3.0GiB / 容量 100.0GiB、一時rootの使用量 20.0GiB" in output.err

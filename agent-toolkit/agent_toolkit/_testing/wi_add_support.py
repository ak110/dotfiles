# pylint: disable=protected-access,subprocess-run-check

import argparse
import contextlib
import pathlib
import subprocess
import tempfile
from collections.abc import Callable
from typing import Any, cast

import pytest

from agent_toolkit import atk
from agent_toolkit._atk.wi import add as add_module
from agent_toolkit._atk.wi import sync as _wi_sync
from agent_toolkit._atk.wi.constants import WI_TYPE_UWI
from agent_toolkit._testing import plan_fixture
from agent_toolkit._testing.git_fakes import fake_git_worktree_remote_response as _fake_git_worktree_remote_response
from agent_toolkit.atk_test import _FIXED_DT, _setup_notes


@pytest.fixture(autouse=True)
def _isolate_managed_temp(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path / "temp"))
    (tmp_path / "temp").mkdir()
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))


def _cmd_add_args(
    tmp_path: pathlib.Path,
    message: str,
    *,
    source: str | None = None,
    entry_type: str = "awi",
    dry_run: bool = False,
    depends_on: list[str] | None = None,
) -> argparse.Namespace:
    body_path = tmp_path / "body.md"
    body_path.write_text(message, encoding="utf-8")
    return argparse.Namespace(
        body_file=[str(body_path)],
        repo_path_override=None,
        target_repo="github.com/example/repo",
        type=entry_type,
        depends_on=depends_on or [],
        source=source,
        scope=None,
        question_type="yes-no" if entry_type == WI_TYPE_UWI else None,
        choices=None,
        dry_run=dry_run,
        compare_opening_file=None,
        opening_output=None,
        subparser=None,
    )


def _run_public_add(
    args: argparse.Namespace,
    notes: pathlib.Path,
    now: object,
    home: pathlib.Path,
) -> None:
    del notes, now
    argv = ["wi", "add", "--target-repo", args.target_repo]
    for body_file in args.body_file:
        argv.extend(("--body-file", body_file))
    if args.type != "awi":
        argv.extend(("--type", args.type))
    if args.question_type is not None:
        argv.extend(("--question-type", args.question_type))
    if args.source is not None:
        argv.extend(("--source", args.source))
    for dependency in args.depends_on:
        argv.extend(("--depends-on", dependency))
    if args.dry_run:
        argv.append("--dry-run")
    try:
        atk.main(argv, home=home, now=_FIXED_DT)
    except SystemExit as error:
        if error.code != 0:
            raise


def _patch_cmd_add_operations(monkeypatch: pytest.MonkeyPatch) -> None:
    """`cmd_add`の対象解決・remote同期・commitを差し替える。"""
    monkeypatch.setattr(
        add_module,
        "resolve_add_target",
        lambda _value: ("github.com/example/repo", None),
    )
    monkeypatch.setattr(_wi_sync, "repo_lock", lambda *_args, **_kwargs: contextlib.nullcontext())
    monkeypatch.setattr(_wi_sync, "pull", lambda _path: None)
    monkeypatch.setattr(_wi_sync, "commit_and_push", lambda *_args, **_kwargs: None)


def _with_cause(body: str, value: str) -> str:
    record = plan_fixture.cause_record().replace(
        plan_fixture.cause_record_line("原因"), plan_fixture.cause_record_line("原因", value), 1
    )
    return f"{body}\n\n## 原因分析\n\n{record}\n\n"


def _choice_argv(tmp_path: pathlib.Path, choices: list[str]) -> list[str]:
    body = tmp_path / "choice.md"
    body.write_text("どの案を選びますか？", encoding="utf-8")
    argv = [
        "wi",
        "add",
        "--target-repo=github.com/example/repo",
        "--type=uwi",
        "--source=agent",
        "--question-type=choice",
        "--body-file",
        str(body),
    ]
    for value in choices:
        argv.extend(["--choices", value])
    return argv


def _prepare_notes(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> pathlib.Path:
    """git操作を無効化したprivate-notesディレクトリを用意する。

    既存テスト（`test_flat_add_operation_is_public`等）と同じ差し替え方針に揃える。
    """
    notes = tmp_path / "private-notes"
    (notes / "2024" / "01").mkdir(parents=True)
    monkeypatch.setattr(_wi_sync, "repo_lock", lambda *_args, **_kwargs: contextlib.nullcontext())
    monkeypatch.setattr(_wi_sync, "pull", lambda _path: None)
    monkeypatch.setattr(_wi_sync, "commit_and_push", lambda *_args, **_kwargs: None)
    return notes


def _patch_add_git(monkeypatch: pytest.MonkeyPatch, repo: pathlib.Path, *, failing_prefix: list[str] | None = None) -> None:
    """対象リポジトリの解決に応答するGit fakeへ差し替える。`failing_prefix`で始まるコマンドは失敗させる。"""

    def fake_run(cmd: list[str], *_args: object, **kwargs: object) -> subprocess.CompletedProcess[Any]:
        resp = _fake_git_worktree_remote_response(cmd, repo, kwargs)
        if resp is not None:
            return resp
        if failing_prefix is not None and cmd[: len(failing_prefix)] == failing_prefix:
            raise subprocess.CalledProcessError(returncode=1, cmd=cmd)
        empty: Any = "" if kwargs.get("text") else b""
        return subprocess.CompletedProcess(cmd, returncode=0, stdout=empty, stderr=empty)

    monkeypatch.setattr(subprocess, "run", fake_run)


def _patch_add_editor(monkeypatch: pytest.MonkeyPatch, body: str) -> None:
    git_run = cast(Callable[..., subprocess.CompletedProcess[Any]], subprocess.run)

    def fake_run(cmd: list[str], *args: object, **kwargs: object) -> subprocess.CompletedProcess[Any]:
        if cmd[0] == "fake-editor":
            pathlib.Path(cmd[1]).write_text(body, encoding="utf-8")
            return subprocess.CompletedProcess(cmd, returncode=0)
        return git_run(cmd, *args, **kwargs)

    monkeypatch.setenv("EDITOR", "fake-editor")
    monkeypatch.setattr(subprocess, "run", fake_run)


def _add_body_input_args(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    body: str,
    input_kind: str,
) -> list[str]:
    if input_kind == "editor":
        _patch_add_editor(monkeypatch, body)
        return ["wi", "add"]
    body_path = tmp_path / "body.md"
    body_path.write_text(body, encoding="utf-8")
    return ["wi", "add", "--body-file", str(body_path)]


def _invoke_add_body_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    body: str,
    *options: str,
) -> None:
    _setup_notes(tmp_path)
    body_path = tmp_path / "body.md"
    body_path.write_text(body, encoding="utf-8")
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kwargs: subprocess.CompletedProcess(cmd, 0, "", ""))
    with pytest.raises(SystemExit) as exc_info:
        atk.main(
            ["wi", "add", "--target-repo", "github.com/example/repo", *options, "--body-file", str(body_path)],
            home=tmp_path,
            now=_FIXED_DT,
        )
    assert exc_info.value.code == 0

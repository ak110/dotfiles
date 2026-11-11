"""WIのmutations content: dry_runのテスト。"""

import pathlib
import re
import subprocess
from collections.abc import Callable

import pytest

from agent_toolkit import atk
from agent_toolkit._testing.wi_bodies import AGENT_AWI_BODY
from agent_toolkit._testing.wi_entry_files import entry_path as _fixed_path
from agent_toolkit._testing.wi_mutations_content_support import (
    _DRY_RUN_SUCCESS,
    _edit_body_args,
    _git_output,
    _prepare_colloquial_body,
    _prepare_legacy_body,
    _prepare_missing_sections,
    _prepare_processing,
    _prepare_reserved_key,
    _prepare_type_change,
    _prepare_undetermined_cause,
    _prepare_user_comment,
    _run_edit,
)
from agent_toolkit._testing.wi_mutations_support import _write_uwi_entry
from agent_toolkit.atk_test import (
    _GitCall,
    _make_subprocess_fake,
    _setup_notes,
    _setup_notes_with_pending_commit,
    _write_awi_file,
)


def test_edit_dry_run_validates_without_changing_notes_or_remote(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """受理される本文では成功の1行だけを書き、private-notesのcommit、remoteのbranch、対象ファイルを変えない。"""
    notes = _setup_notes_with_pending_commit(tmp_path, pending=False)
    monkeypatch.setenv("AGENT_TOOLKIT_PRIVATE_NOTES", str(notes))
    monkeypatch.setenv("CLAUDECODE", "1")
    filename = "20261007-010614-901.md"
    path = _write_awi_file(notes, filename, body=AGENT_AWI_BODY, source="test")
    _git_output(notes, "add", ".")
    _git_output(notes, "-c", "user.name=atk-test", "-c", "user.email=atk-test@example.invalid", "commit", "-m", "awi")
    _git_output(notes, "push", "origin", "main")
    origin = tmp_path / "origin.git"
    before = (_git_output(notes, "rev-parse", "HEAD"), _git_output(origin, "rev-parse", "main"), path.read_bytes())
    args = [*_edit_body_args(tmp_path, filename, AGENT_AWI_BODY + "\n補足の段落。"), "--target-repo=github.com/example/foo"]

    with pytest.raises(SystemExit) as dry_run:
        atk.main([*args, "--dry-run"], home=tmp_path)

    output = capsys.readouterr()
    assert dry_run.value.code == 0, output.err
    assert f"{_DRY_RUN_SUCCESS}: {filename}" in output.out
    assert "冒頭保存先:" in output.out
    assert (_git_output(notes, "rev-parse", "HEAD"), _git_output(origin, "rev-parse", "main"), path.read_bytes()) == before
    assert _git_output(notes, "status", "--porcelain=v1") == ""

    # 同じ本文は保存でも受理される。
    with pytest.raises(SystemExit) as saved:
        atk.main(args, home=tmp_path)
    assert saved.value.code == 0, capsys.readouterr().err
    assert "補足の段落。" in path.read_text(encoding="utf-8")
    assert _git_output(origin, "rev-parse", "main") != before[1]


@pytest.mark.parametrize("newline", ["\r\n", "\r", "\n"])
@pytest.mark.parametrize("entry_type", ["awi", "uwi"])
def test_edit_dry_run_opening_and_unchanged_input(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    newline: str,
    entry_type: str,
) -> None:
    """差分なしのAWIとUWIも入力原稿の冒頭を原文のまま返す。"""
    notes = _setup_notes(tmp_path)
    monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))
    expected = "# 表題\n\n問いと理由。  \n推奨回答。\n\n```text\n## 引用\n```\n\n\n".replace("\n", newline)
    body = expected + "## 補足\n確認\n".replace("\n", newline)
    if entry_type == "awi":
        path = _write_awi_file(notes, "opening.md", body=body)
    else:
        path = _write_uwi_entry(notes, "opening.md", question=body)
    args = _edit_body_args(tmp_path, path.name, body)
    source = pathlib.Path(args[args.index("--body-file") + 1])
    source.write_bytes(body.encode())
    before = path.read_bytes()
    for _attempt in range(2):
        with pytest.raises(SystemExit) as finished:
            atk.main([*args, "--dry-run"], home=tmp_path)
        output = capsys.readouterr()
        assert finished.value.code == 0, output.err
        saved = re.search(r"^冒頭保存先: (.+)$", output.out, re.MULTILINE)
        assert saved is not None
        assert pathlib.Path(saved[1]).read_bytes() == expected.encode()
        assert expected in output.out
        assert path.read_bytes() == before
        assert source.read_bytes() == body.encode()


@pytest.mark.parametrize(
    ("prepare", "expected_code"),
    [
        (_prepare_reserved_key, 1),
        (_prepare_type_change, 2),
        (_prepare_user_comment, 1),
        (_prepare_missing_sections, 1),
        (_prepare_undetermined_cause, 1),
        (_prepare_processing, 2),
        (_prepare_legacy_body, 0),
        (_prepare_colloquial_body, 0),
    ],
)
def test_edit_dry_run_matches_save_judgement(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    prepare: Callable[[pathlib.Path, pytest.MonkeyPatch], tuple[str, str]],
    expected_code: int,
) -> None:
    """拒否の種類ごとに`--dry-run`と保存へ同じ入力を渡し、終了コード、理由、次の操作が一致する。

    受理される旧書式の本文と口語を含む本文では、保存が本文を書き換え、`--dry-run`は書き換えない。
    `--dry-run`のための検証を別に書き起こすと、保存側だけに加えた検証との差がここで現れる。
    """
    monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))
    results = {}
    for mode in ("save", "dry-run"):
        home = tmp_path / mode
        home.mkdir()
        notes = _setup_notes(home)
        monkeypatch.setenv("AGENT_TOOLKIT_PRIVATE_NOTES", str(notes))
        filename, message = prepare(notes, monkeypatch)
        target = _fixed_path(notes, filename)
        original = target.read_bytes()
        argv = _edit_body_args(home, filename, message)
        code, error, output = _run_edit(home, [*argv, "--dry-run"] if mode == "dry-run" else argv, capsys)
        results[mode] = (code, error)
        if mode == "dry-run" or code != 0:
            assert target.read_bytes() == original
        if mode == "dry-run" and code == 0:
            assert f"{_DRY_RUN_SUCCESS}: {filename}" in output
            assert any(line.startswith("冒頭保存先:") for line in output)

    assert results["save"][0] == expected_code
    assert results["dry-run"] == results["save"]
    if expected_code:
        assert "次の操作: " in results["save"][1]


@pytest.mark.parametrize(
    "extra",
    [
        ["--dry-run", "--append"],
        ["--dry-run", "--cooldown-until", "2026-10-08T00:00:00+09:00"],
    ],
)
def test_edit_dry_run_rejects_unsupported_combinations(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    extra: list[str],
) -> None:
    """`--append`・`--cooldown-until`との併用と`--body-file`なしの指定はusage errorで終了し、何も保存しない。"""
    notes = _setup_notes(tmp_path)
    path = _write_awi_file(notes, "fb-001.md")
    original = path.read_bytes()
    calls: list[_GitCall] = []
    monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(calls))

    for argv in ([*_edit_body_args(tmp_path, "fb-001.md", "編集後"), *extra], ["wi", "edit", "fb-001.md", "--dry-run"]):
        with pytest.raises(SystemExit) as result:
            atk.main(argv, home=tmp_path)
        assert result.value.code == 2
        assert "--dry-run" in capsys.readouterr().err
    assert path.read_bytes() == original
    assert not [call for call in calls if {"commit", "push"} & set(call["cmd"])]


def test_edit_help_describes_dry_run(capsys: pytest.CaptureFixture[str]) -> None:
    """`atk wi edit --help`が`--dry-run`と、検証だけを行いprivate-notesとremoteを変えないことを示す。"""
    with pytest.raises(SystemExit):
        atk.main(["wi", "edit", "--help"])
    output = "".join(capsys.readouterr().out.split())
    assert "--dry-run" in output
    assert "保存と同じ検証にかけ、保存せずに結果を返す" in output
    assert "private-notesとremoteを変えない" in output

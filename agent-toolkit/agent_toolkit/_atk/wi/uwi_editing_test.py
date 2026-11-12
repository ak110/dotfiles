# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。
"""WIのuwi: editingのテスト。"""

import pathlib
import subprocess

import pytest

from agent_toolkit import atk
from agent_toolkit._atk.wi import uwi as uwi_module
from agent_toolkit._testing.wi_entry_files import entry_path as _fixed_path
from agent_toolkit._testing.wi_uwi_support import _body_file_args, _invoke_uwi_add, _make_uwi_add_fake
from agent_toolkit._testing.wi_uwi_support import _clear_agent_environment as _clear_agent_environment
from agent_toolkit.atk_test import (
    _FIXED_TIMESTAMP,
    _GitCall,
    _make_subprocess_fake,
    _setup_notes,
    _write_awi_file,
    _write_uwi_file,
)


class TestUwiMutationTargetRepoVerification:
    """UWIのedit・adopt・rm: `--target-repo`指定時のfrontmatter一致検証を検証する。

    テストの準備で書くfrontmatterの`target_repo`は`github.com/example/foo`（`_write_uwi_file`で指定を省いた場合の値）。
    """

    def test_uwi_edit_mismatch_exits_2(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """edit: `--target-repo`不一致時にexit 2でエディターは起動されない。"""
        notes = _setup_notes(tmp_path)
        _write_uwi_file(notes, f"{_FIXED_TIMESTAMP}-001.md", question="q")
        monkeypatch.setenv("EDITOR", "fake-editor")
        editor_calls: list[list[str]] = []

        def fake_run(cmd: list[str], *_a: object, **_kw: object) -> subprocess.CompletedProcess[bytes]:
            if cmd[0] == "fake-editor":
                editor_calls.append(list(cmd))
            return subprocess.CompletedProcess(cmd, returncode=0, stdout=b"", stderr=b"")

        monkeypatch.setattr(subprocess, "run", fake_run)

        with pytest.raises(SystemExit) as exc_info:
            atk.main(
                ["wi", "edit", f"{_FIXED_TIMESTAMP}-001.md", "--target-repo", "github.com/other/repo"],
                home=tmp_path,
            )

        assert exc_info.value.code == 2
        assert not editor_calls

    def test_uwi_adopt_mismatch_exits_2(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """adopt: `--target-repo`不一致時にexit 2でファイルは移動されない。"""
        notes = _setup_notes(tmp_path)
        _write_uwi_file(notes, f"{_FIXED_TIMESTAMP}-001.md", question="q", answer="はい")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(
                ["wi", "adopt", f"{_FIXED_TIMESTAMP}-001.md", "--target-repo", "github.com/other/repo"],
                home=tmp_path,
            )

        assert exc_info.value.code == 2
        assert "state: inbox\n" in _fixed_path(notes, f"{_FIXED_TIMESTAMP}-001.md").read_text(encoding="utf-8")

    def test_uwi_adopt_match_succeeds(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """adopt: `--target-repo`一致時は通常通りadopted/へ移動する。"""
        notes = _setup_notes(tmp_path)
        _write_uwi_file(notes, f"{_FIXED_TIMESTAMP}-001.md", question="q", answer="はい")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(
                ["wi", "adopt", f"{_FIXED_TIMESTAMP}-001.md", "--target-repo", "github.com/example/foo"],
                home=tmp_path,
            )

        assert exc_info.value.code == 0
        assert "state: adopted\n" in _fixed_path(notes, f"{_FIXED_TIMESTAMP}-001.md").read_text(encoding="utf-8")

    def test_uwi_rm_mismatch_exits_2(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """rm: `--target-repo`不一致時にexit 2でファイルは削除されない。"""
        notes = _setup_notes(tmp_path)
        _write_uwi_file(notes, f"{_FIXED_TIMESTAMP}-001.md")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(
                ["wi", "rm", f"{_FIXED_TIMESTAMP}-001.md", "--target-repo", "github.com/other/repo"],
                home=tmp_path,
            )

        assert exc_info.value.code == 2
        assert "state: inbox\n" in _fixed_path(notes, f"{_FIXED_TIMESTAMP}-001.md").read_text(encoding="utf-8")


@pytest.mark.parametrize("skip_pull", [False, True])
def test_uwi_list_filters_answers_and_honors_sync_option(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    skip_pull: bool,
) -> None:
    """回答済みUWIとAWIを除外し、同期省略時も同じ未回答本文を表示する。"""
    notes = _setup_notes(tmp_path)
    _write_uwi_file(notes, f"{_FIXED_TIMESTAMP}-001.md", question="q1", answer="")
    _write_uwi_file(notes, f"{_FIXED_TIMESTAMP}-002.md", question="q2", answer="回答あり\n")
    _write_awi_file(notes, "awi.md")
    calls: list[_GitCall] = []
    monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(calls))
    with pytest.raises(SystemExit) as result:
        atk.main(
            ["wi", "list", "--type=uwi", "--answered=no", "--no-jsonl", *(["--skip-pull"] if skip_pull else [])], home=tmp_path
        )
    assert result.value.code == 0
    assert capsys.readouterr().out == f"# uwi\n{_FIXED_TIMESTAMP}-001.md: github.com/example/foo [inbox/unanswered] q1\n"
    synchronized = any(call["cmd"][:2] == ["git", "fetch"] for call in calls)
    assert synchronized is not skip_pull


class TestUwiEdit:
    """UWI編集の境界条件検証。"""

    def test_show_body_can_be_reused_without_duplicate_headings(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """showで取得した未回答の本文をそのまま編集入力へ戻せる。"""
        _setup_notes(tmp_path)
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()
        monkeypatch.setattr(subprocess, "run", _make_uwi_add_fake(myrepo))
        assert _invoke_uwi_add(tmp_path, str(myrepo), body="この方針を採用しますか？").code == 0
        filename = f"{_FIXED_TIMESTAMP}-001.md"
        capsys.readouterr()
        with pytest.raises(SystemExit) as shown:
            atk.main(["wi", "show", filename, "--skip-pull"], home=tmp_path)
        assert shown.value.code == 0
        shown_text = capsys.readouterr().out
        question_body = shown_text[shown_text.index(uwi_module.QUESTION_HEADING) :]
        with pytest.raises(SystemExit) as edited:
            atk.main(["wi", "edit", filename, *_body_file_args(tmp_path, question_body)], home=tmp_path)
        assert edited.value.code == 0
        assert "差分なし" in capsys.readouterr().out

    def test_rejects_traversal(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """パストラバーサル系のファイル名でexit 2。"""
        _setup_notes(tmp_path)
        monkeypatch.setenv("EDITOR", "vi")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "edit", "../escape.md"], home=tmp_path)
        assert exc_info.value.code == 2

    def test_no_diff_skips_commit(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """編集差分なしの場合はcommit・pushしない。"""
        notes = _setup_notes(tmp_path)
        _write_uwi_file(notes, f"{_FIXED_TIMESTAMP}-001.md", question="q")
        monkeypatch.setenv("EDITOR", "vi")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "edit", f"{_FIXED_TIMESTAMP}-001.md"], home=tmp_path)
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "成功: 差分なし（変更は無い）" in captured.out
        commit_calls = [c for c in git_calls if c["cmd"][:2] == ["git", "commit"]]
        assert commit_calls == []

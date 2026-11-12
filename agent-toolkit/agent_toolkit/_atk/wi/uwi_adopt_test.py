# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。
"""WIのuwi: adoptのテスト。"""

import pathlib
import subprocess
from typing import Any

import pytest

from agent_toolkit import atk
from agent_toolkit._testing.wi_entry_files import entry_path as _fixed_path
from agent_toolkit._testing.wi_uwi_support import _clear_agent_environment as _clear_agent_environment
from agent_toolkit.atk_test import _FIXED_TIMESTAMP, _GitCall, _make_subprocess_fake, _setup_notes, _write_uwi_file


class TestUwiAdopt:
    """UWI採用: inboxからadopted/へ移動しコミットする。"""

    def test_single_file_adopted(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """1件の`atk wi adopt`実行でinboxから移動されadopted/に置かれコミットメッセージが正しいこと。"""
        notes = _setup_notes(tmp_path)
        _write_uwi_file(notes, f"{_FIXED_TIMESTAMP}-001.md", question="q", answer="はい")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "adopt", f"{_FIXED_TIMESTAMP}-001.md"], home=tmp_path)

        assert exc_info.value.code == 0
        assert "state: adopted\n" in _fixed_path(notes, f"{_FIXED_TIMESTAMP}-001.md").read_text(encoding="utf-8")

        commit_cmd = [c["cmd"] for c in git_calls if "commit" in c["cmd"]][0]
        assert "chore: process 1 entry (adopted)" in commit_cmd

    def test_commit_without_resolvable_worktree_stops_before_mutation(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """対応commitを検証できない場合は、UWIを移動せず終了する。"""
        notes = _setup_notes(tmp_path)
        _write_uwi_file(notes, f"{_FIXED_TIMESTAMP}-001.md", question="q", answer="はい")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(
                [
                    "wi",
                    "adopt",
                    f"{_FIXED_TIMESTAMP}-001.md",
                    "--note",
                    "UWI採用メモ",
                    "--commit",
                    "xyz9876",
                ],
                home=tmp_path,
            )

        assert exc_info.value.code == 2
        assert "state: inbox\n" in _fixed_path(notes, f"{_FIXED_TIMESTAMP}-001.md").read_text(encoding="utf-8")
        assert "対応commitを検証できる対象リポジトリ" in capsys.readouterr().err

    def test_short_commit_is_resolved_with_explicit_worktree(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """明示作業ツリーに対応するUWIではcommitの一意な長さの短縮OIDと件名を記録する。"""
        notes = _setup_notes(tmp_path)
        worktree = tmp_path / "worktree"
        worktree.mkdir()
        _write_uwi_file(notes, f"{_FIXED_TIMESTAMP}-001.md", question="q", answer="はい")
        git_calls: list[_GitCall] = []
        base_fake = _make_subprocess_fake(git_calls)
        full_oid = "c" * 40
        subject = "fix: UWI終了記録を安定化する"

        def fake_run(cmd: list[str], *args: object, **kwargs: object) -> subprocess.CompletedProcess[Any]:
            if cmd == ["git", "-C", str(worktree), "remote", "get-url", "origin"]:
                return subprocess.CompletedProcess(cmd, 0, "https://github.com/example/foo.git\n", "")
            if cmd == [
                "git",
                "-C",
                str(worktree),
                "rev-parse",
                "--verify",
                "--end-of-options",
                "abcdef1^{commit}",
            ]:
                return subprocess.CompletedProcess(cmd, 0, full_oid + "\n", "")
            if cmd == [
                "git",
                "-C",
                str(worktree),
                "show",
                "-s",
                "--format=%h%x00%s",
                full_oid,
            ]:
                return subprocess.CompletedProcess(cmd, 0, f"{full_oid[:9]}\0{subject}\n", "")
            return base_fake(cmd, *args, **kwargs)

        monkeypatch.setattr(subprocess, "run", fake_run)
        with pytest.raises(SystemExit) as exc_info:
            atk.main(
                [
                    "wi",
                    "adopt",
                    f"{_FIXED_TIMESTAMP}-001.md",
                    "--target-repo",
                    str(worktree),
                    "--commit=abcdef1",
                ],
                home=tmp_path,
            )
        assert exc_info.value.code == 0
        content = _fixed_path(notes, f"{_FIXED_TIMESTAMP}-001.md").read_text(encoding="utf-8")
        assert "- 対応commit作成者日時:" not in content
        assert f"- 対応commit件名: {subject}" in content
        assert f"- 対応commit: {full_oid[:9]}\n" in content
        assert full_oid not in content

    def test_multiple_files_adopted_single_commit(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """3件の`atk wi adopt`で全件がadopted/へ移動し単一コミットが行われること。"""
        notes = _setup_notes(tmp_path)
        _write_uwi_file(notes, f"{_FIXED_TIMESTAMP}-001.md", question="q1", answer="a1")
        _write_uwi_file(notes, f"{_FIXED_TIMESTAMP}-002.md", question="q2", answer="a2")
        _write_uwi_file(notes, f"{_FIXED_TIMESTAMP}-003.md", question="q3", answer="a3")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(
                [
                    "wi",
                    "adopt",
                    f"{_FIXED_TIMESTAMP}-001.md",
                    f"{_FIXED_TIMESTAMP}-002.md",
                    f"{_FIXED_TIMESTAMP}-003.md",
                ],
                home=tmp_path,
            )

        assert exc_info.value.code == 0
        for name in (f"{_FIXED_TIMESTAMP}-001.md", f"{_FIXED_TIMESTAMP}-002.md", f"{_FIXED_TIMESTAMP}-003.md"):
            assert "state: adopted\n" in _fixed_path(notes, name).read_text(encoding="utf-8")

        commit_calls = [c["cmd"] for c in git_calls if c["cmd"][:2] == ["git", "commit"]]
        assert len(commit_calls) == 1
        assert "chore: process 3 entries (adopted)" in commit_calls[0]

    def test_pushes_after_commit(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """UWI採用の実行後にgit pushが行われること。"""
        notes = _setup_notes(tmp_path)
        _write_uwi_file(notes, f"{_FIXED_TIMESTAMP}-001.md", question="q", answer="はい")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "adopt", f"{_FIXED_TIMESTAMP}-001.md"], home=tmp_path)

        assert exc_info.value.code == 0
        assert any(c["cmd"] == ["git", "push"] for c in git_calls)

    def test_rejects_traversal(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """パストラバーサル系のファイル名でexit 2。"""
        _setup_notes(tmp_path)
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "adopt", "../escape.md"], home=tmp_path)

        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        assert "不正なファイル名" in captured.err or "基準ディレクトリ外" in captured.err

    def test_missing_file_exits(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """processing・inbox・holdのいずれにも存在しないファイル名指定でexit 2と案内が出力される。"""
        _setup_notes(tmp_path)
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "adopt", "nonexistent.md"], home=tmp_path)

        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        assert "processing・inbox・holdに存在しない" in captured.err

    def test_partial_missing_file_prevents_any_move(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """複数ファイル指定時、一部が未存在ならどのファイルも移動されない（部分移動防止）。"""
        notes = _setup_notes(tmp_path)
        _write_uwi_file(notes, f"{_FIXED_TIMESTAMP}-001.md", question="q1", answer="a1")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(
                ["wi", "adopt", f"{_FIXED_TIMESTAMP}-001.md", "nonexistent.md"],
                home=tmp_path,
            )

        assert exc_info.value.code == 2
        assert "state: inbox\n" in _fixed_path(notes, f"{_FIXED_TIMESTAMP}-001.md").read_text(encoding="utf-8")

    def test_duplicate_filenames_deduplicated_without_warning(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """同一ファイル名を重複指定した場合、初出の1件へ集約して移動する。

        重複除去前は`src.rename(dst)`が1回目の成功後に2回目で対象不在となり
        `FileNotFoundError`のTracebackが露出していた。
        """
        notes = _setup_notes(tmp_path)
        _write_uwi_file(notes, f"{_FIXED_TIMESTAMP}-001.md", question="q", answer="はい")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(
                ["wi", "adopt", f"{_FIXED_TIMESTAMP}-001.md", f"{_FIXED_TIMESTAMP}-001.md"],
                home=tmp_path,
            )

        assert exc_info.value.code == 0
        assert "state: adopted\n" in _fixed_path(notes, f"{_FIXED_TIMESTAMP}-001.md").read_text(encoding="utf-8")
        stderr = capsys.readouterr().err
        assert "重複がある" not in stderr
        commit_calls = [c["cmd"] for c in git_calls if c["cmd"][:2] == ["git", "commit"]]
        assert len(commit_calls) == 1
        assert "chore: process 1 entry (adopted)" in commit_calls[0]


class TestUwiRm:
    """UWI削除の単体テスト。"""

    def test_single_file_removed(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """1件の`atk wi rm`実行でinbox配下ファイルが削除されコミットメッセージが正しいこと。"""
        notes = _setup_notes(tmp_path)
        _write_uwi_file(notes, f"{_FIXED_TIMESTAMP}-001.md")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))
        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "rm", f"{_FIXED_TIMESTAMP}-001.md"], home=tmp_path)
        assert exc_info.value.code == 0
        assert not _fixed_path(notes, f"{_FIXED_TIMESTAMP}-001.md").exists()
        commit_cmd = [c["cmd"] for c in git_calls if "commit" in c["cmd"]][0]
        assert "chore: remove 1 entry" in " ".join(commit_cmd)

    def test_note_included_in_commit_message(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """`--note`指定時にcommit messageへ`(理由: <note>)`形式で追記されること。"""
        notes = _setup_notes(tmp_path)
        _write_uwi_file(notes, f"{_FIXED_TIMESTAMP}-001.md")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))
        with pytest.raises(SystemExit) as exc_info:
            atk.main(
                ["wi", "rm", f"{_FIXED_TIMESTAMP}-001.md", "--note", "誤投入"],
                home=tmp_path,
            )
        assert exc_info.value.code == 0
        commit_cmd = [c["cmd"] for c in git_calls if "commit" in c["cmd"]][0]
        assert "(理由: 誤投入)" in " ".join(commit_cmd)

    def test_rejects_traversal(
        self,
        tmp_path: pathlib.Path,
    ) -> None:
        """パストラバーサル文字列は削除前検証で拒否されること。"""
        _setup_notes(tmp_path)
        with pytest.raises(SystemExit):
            atk.main(["wi", "rm", "../evil.md"], home=tmp_path)

    def test_missing_file_exits(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """指定ファイルがinbox配下に存在しないときexit 2で終了すること。"""
        _setup_notes(tmp_path)
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))
        with pytest.raises(SystemExit):
            atk.main(
                ["wi", "rm", f"{_FIXED_TIMESTAMP}-999.md"],
                home=tmp_path,
            )

    def test_partial_missing_file_prevents_any_removal(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """複数指定で一部欠損時に既存ファイルも削除されずcommitも発生しないこと。"""
        notes = _setup_notes(tmp_path)
        existing = _write_uwi_file(notes, f"{_FIXED_TIMESTAMP}-001.md")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))
        with pytest.raises(SystemExit):
            atk.main(
                [
                    "wi",
                    "rm",
                    f"{_FIXED_TIMESTAMP}-001.md",
                    f"{_FIXED_TIMESTAMP}-999.md",
                ],
                home=tmp_path,
            )
        assert existing.exists()
        assert not [c["cmd"] for c in git_calls if "commit" in c["cmd"]]

    def test_duplicate_filenames_deduplicated_without_warning(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """同一ファイル名を重複指定した場合、初出の1件へ集約して削除する。

        重複除去前は`p.unlink()`が1回目の成功後に2回目で対象不在となり
        `FileNotFoundError`のTracebackが露出していた。
        """
        notes = _setup_notes(tmp_path)
        _write_uwi_file(notes, f"{_FIXED_TIMESTAMP}-001.md")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))
        with pytest.raises(SystemExit) as exc_info:
            atk.main(
                ["wi", "rm", f"{_FIXED_TIMESTAMP}-001.md", f"{_FIXED_TIMESTAMP}-001.md"],
                home=tmp_path,
            )
        assert exc_info.value.code == 0
        assert not _fixed_path(notes, f"{_FIXED_TIMESTAMP}-001.md").exists()
        stderr = capsys.readouterr().err
        assert "重複がある" not in stderr
        commit_cmd = [c["cmd"] for c in git_calls if "commit" in c["cmd"]][0]
        assert "chore: remove 1 entry" in " ".join(commit_cmd)

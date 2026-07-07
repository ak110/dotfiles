"""pytools.dotfiles_fb._cli のtbd系サブコマンドのテスト。

tbd-add/tbd-list/tbd-edit/tbd-answer/tbd-adopt/tbd-rmサブコマンドの単体テストを集約する。
既存サブコマンドのテストは`_cli_test.py`に、拡張サブコマンド・オプションのテストは
`_cli_extras_test.py`に分離する。共通ヘルパーは`_cli_test.py`から再利用する。
"""

import pathlib
import subprocess
from typing import Any

import pytest

from pytools.dotfiles_fb import _cli
from pytools.dotfiles_fb._cli_test import (
    _FIXED_DT,
    _FIXED_TIMESTAMP,
    _GitCall,
    _make_subprocess_fake,
    _setup_tbd_env,
    _write_tbd_file,
)


class TestTbdAdd:
    """tbd-addサブコマンドの基本動作検証。"""

    def test_single_message_generates_one_file(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """単一メッセージで1ファイルが生成され、frontmatter・本文構造が正しい。"""
        notes = _setup_tbd_env(tmp_path)
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()

        def fake_run(cmd: list[str], *_a: object, **kw: object) -> subprocess.CompletedProcess[Any]:
            if cmd == ["git", "-C", str(myrepo), "remote", "get-url", "origin"]:
                stdout: Any = (
                    "https://github.com/example/myrepo.git\n" if kw.get("text") else b"https://github.com/example/myrepo.git\n"
                )
                return subprocess.CompletedProcess(cmd, returncode=0, stdout=stdout, stderr="" if kw.get("text") else b"")
            empty: Any = "" if kw.get("text") else b""
            return subprocess.CompletedProcess(cmd, returncode=0, stdout=empty, stderr=empty)

        monkeypatch.setattr(subprocess, "run", fake_run)

        with pytest.raises(SystemExit) as exc_info:
            _cli.main(
                ["tbd-add", str(myrepo), "--scope", "theme1", "未確認の挙動"],
                home=tmp_path,
                now=_FIXED_DT,
            )
        assert exc_info.value.code == 0

        files = sorted((notes / "tbd" / "inbox").iterdir())
        assert len(files) == 1
        content = files[0].read_text(encoding="utf-8")
        assert "target_repo: github.com/example/myrepo" in content
        assert "scope: theme1" in content
        assert "question_type: free" in content
        assert "created:" not in content.split("---\n\n", 1)[0]
        assert "## 質問\n\n未確認の挙動" in content
        assert "## 回答" in content

    def test_choice_requires_choices(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """question-type=choice時に--choices未指定でexit 2。"""
        _setup_tbd_env(tmp_path)
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()

        def fake_run(cmd: list[str], *_a: object, **kw: object) -> subprocess.CompletedProcess[Any]:
            if cmd[:5] == ["git", "-C", str(myrepo), "remote", "get-url"]:
                stdout: Any = "https://github.com/example/myrepo.git\n" if kw.get("text") else b""
                return subprocess.CompletedProcess(cmd, returncode=0, stdout=stdout, stderr="" if kw.get("text") else b"")
            empty: Any = "" if kw.get("text") else b""
            return subprocess.CompletedProcess(cmd, returncode=0, stdout=empty, stderr=empty)

        monkeypatch.setattr(subprocess, "run", fake_run)

        with pytest.raises(SystemExit) as exc_info:
            _cli.main(
                ["tbd-add", str(myrepo), "--question-type", "choice", "q"],
                home=tmp_path,
                now=_FIXED_DT,
            )
        assert exc_info.value.code == 2


class TestTbdList:
    """tbd-listサブコマンドのフィルター動作検証。"""

    def test_status_filter(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """--status=unansweredで未回答のみが1件1行（filename・target_repo・summary）形式で出力される。"""
        notes = _setup_tbd_env(tmp_path)
        _write_tbd_file(notes, f"{_FIXED_TIMESTAMP}-001.md", question="q1", answer="")
        _write_tbd_file(notes, f"{_FIXED_TIMESTAMP}-002.md", question="q2", answer="回答あり\n")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            _cli.main(["tbd-list", "--status", "unanswered"], home=tmp_path)
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert captured.out == f"# tbd\n{_FIXED_TIMESTAMP}-001.md\tgithub.com/example/foo\t[unanswered] q1\n"


class TestTbdListSkipPull:
    """tbd-listサブコマンド: --skip-pull指定時はgit pullをスキップする。"""

    def test_skip_pull_omits_git_pull(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """--skip-pull指定時はgit pull --ff-onlyが実行されない。"""
        notes = _setup_tbd_env(tmp_path)
        _write_tbd_file(notes, f"{_FIXED_TIMESTAMP}-001.md", question="q1")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            _cli.main(["tbd-list", "--skip-pull"], home=tmp_path)

        assert exc_info.value.code == 0
        assert not any(c["cmd"][:2] == ["git", "pull"] for c in git_calls)


class TestTbdEdit:
    """tbd-editサブコマンドの境界条件検証。"""

    def test_rejects_traversal(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """パストラバーサル系のファイル名でexit 2。"""
        _setup_tbd_env(tmp_path)
        monkeypatch.setenv("EDITOR", "vi")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            _cli.main(["tbd-edit", "../escape.md"], home=tmp_path)
        assert exc_info.value.code == 2

    def test_no_diff_skips_commit(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """編集差分なしの場合はcommit・pushしない。"""
        notes = _setup_tbd_env(tmp_path)
        _write_tbd_file(notes, f"{_FIXED_TIMESTAMP}-001.md", question="q")
        monkeypatch.setenv("EDITOR", "vi")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            _cli.main(["tbd-edit", f"{_FIXED_TIMESTAMP}-001.md"], home=tmp_path)
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "差分なし" in captured.out
        commit_calls = [c for c in git_calls if c["cmd"][:2] == ["git", "commit"]]
        assert commit_calls == []


class TestTbdAnswer:
    """tbd-answerサブコマンドの空集合・差分なし時の挙動検証。"""

    def test_no_unanswered_prints_message(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """未回答ゼロ時は案内のみでcommitしない。"""
        notes = _setup_tbd_env(tmp_path)
        _write_tbd_file(notes, f"{_FIXED_TIMESTAMP}-001.md", question="q", answer="ans\n")
        monkeypatch.setenv("EDITOR", "vi")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            _cli.main(["tbd-answer"], home=tmp_path)
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "未回答のTBDはありません" in captured.out
        commit_calls = [c for c in git_calls if c["cmd"][:2] == ["git", "commit"]]
        assert commit_calls == []


class TestTbdAdopt:
    """tbd-adoptサブコマンド: 採用としてtbd/inboxからtbd/adopted/へ移動しコミットを行う。"""

    def test_single_file_adopted(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """1件のtbd-adopt実行でtbd/inboxから移動されtbd/adopted/に置かれコミットメッセージが正しいこと。"""
        notes = _setup_tbd_env(tmp_path)
        _write_tbd_file(notes, f"{_FIXED_TIMESTAMP}-001.md", question="q", answer="はい")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            _cli.main(["tbd-adopt", f"{_FIXED_TIMESTAMP}-001.md"], home=tmp_path)

        assert exc_info.value.code == 0
        assert not (notes / "tbd" / "inbox" / f"{_FIXED_TIMESTAMP}-001.md").exists()
        assert (notes / "tbd" / "adopted" / f"{_FIXED_TIMESTAMP}-001.md").exists()

        commit_cmd = [c["cmd"] for c in git_calls if "commit" in c["cmd"]][0]
        assert "chore: adopt 1 tbd item" in commit_cmd

    def test_stamp_written_with_all_fields(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """--note・--commit指定時、tbd/adopted/配下のファイル末尾に採否・処理日時・対応commit・メモが追記される。"""
        notes = _setup_tbd_env(tmp_path)
        _write_tbd_file(notes, f"{_FIXED_TIMESTAMP}-001.md", question="q", answer="はい")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            _cli.main(
                [
                    "tbd-adopt",
                    f"{_FIXED_TIMESTAMP}-001.md",
                    "--note",
                    "TBD採用メモ",
                    "--commit",
                    "xyz9876",
                ],
                home=tmp_path,
            )

        assert exc_info.value.code == 0
        adopted_text = (notes / "tbd" / "adopted" / f"{_FIXED_TIMESTAMP}-001.md").read_text(encoding="utf-8")
        assert "## 処理結果" in adopted_text
        assert "- 採否: tbd-adopted" in adopted_text
        assert "- 処理日時: " in adopted_text
        assert "- 対応commit: xyz9876" in adopted_text
        assert "- メモ: TBD採用メモ" in adopted_text

    def test_multiple_files_adopted_single_commit(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """3件のtbd-adoptで全件がtbd/adopted/へ移動し単一コミットが行われること。"""
        notes = _setup_tbd_env(tmp_path)
        _write_tbd_file(notes, f"{_FIXED_TIMESTAMP}-001.md", question="q1", answer="a1")
        _write_tbd_file(notes, f"{_FIXED_TIMESTAMP}-002.md", question="q2", answer="a2")
        _write_tbd_file(notes, f"{_FIXED_TIMESTAMP}-003.md", question="q3", answer="a3")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            _cli.main(
                [
                    "tbd-adopt",
                    f"{_FIXED_TIMESTAMP}-001.md",
                    f"{_FIXED_TIMESTAMP}-002.md",
                    f"{_FIXED_TIMESTAMP}-003.md",
                ],
                home=tmp_path,
            )

        assert exc_info.value.code == 0
        for name in (f"{_FIXED_TIMESTAMP}-001.md", f"{_FIXED_TIMESTAMP}-002.md", f"{_FIXED_TIMESTAMP}-003.md"):
            assert not (notes / "tbd" / "inbox" / name).exists()
            assert (notes / "tbd" / "adopted" / name).exists()

        commit_calls = [c["cmd"] for c in git_calls if c["cmd"][:2] == ["git", "commit"]]
        assert len(commit_calls) == 1
        assert "chore: adopt 3 tbd items" in commit_calls[0]

    def test_pushes_after_commit(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """tbd-adopt実行後にgit pushが行われること。"""
        notes = _setup_tbd_env(tmp_path)
        _write_tbd_file(notes, f"{_FIXED_TIMESTAMP}-001.md", question="q", answer="はい")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            _cli.main(["tbd-adopt", f"{_FIXED_TIMESTAMP}-001.md"], home=tmp_path)

        assert exc_info.value.code == 0
        assert any(c["cmd"] == ["git", "push"] for c in git_calls)

    def test_rejects_traversal(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """パストラバーサル系のファイル名でexit 2。"""
        _setup_tbd_env(tmp_path)
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            _cli.main(["tbd-adopt", "../escape.md"], home=tmp_path)

        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        assert "不正なファイル名" in captured.err or "基準ディレクトリ外" in captured.err

    def test_missing_file_exits(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """tbd/inboxに存在しないファイル名指定でexit 2と案内が出力される。"""
        _setup_tbd_env(tmp_path)
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            _cli.main(["tbd-adopt", "nonexistent.md"], home=tmp_path)

        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        assert "tbd/inboxに存在しません" in captured.err

    def test_partial_missing_file_prevents_any_move(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """複数ファイル指定時、一部が未存在ならどのファイルも移動されない（部分移動防止）。"""
        notes = _setup_tbd_env(tmp_path)
        _write_tbd_file(notes, f"{_FIXED_TIMESTAMP}-001.md", question="q1", answer="a1")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            _cli.main(
                ["tbd-adopt", f"{_FIXED_TIMESTAMP}-001.md", "nonexistent.md"],
                home=tmp_path,
            )

        assert exc_info.value.code == 2
        assert (notes / "tbd" / "inbox" / f"{_FIXED_TIMESTAMP}-001.md").exists()
        assert not (notes / "tbd" / "adopted" / f"{_FIXED_TIMESTAMP}-001.md").exists()


class TestTbdRm:
    """tbd-rmサブコマンドの単体テスト。"""

    def test_single_file_removed(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """1件のtbd-rm実行でinbox配下ファイルが削除されコミットメッセージが正しいこと。"""
        notes = _setup_tbd_env(tmp_path)
        _write_tbd_file(notes, f"{_FIXED_TIMESTAMP}-001.md")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))
        with pytest.raises(SystemExit) as exc_info:
            _cli.main(["tbd-rm", f"{_FIXED_TIMESTAMP}-001.md"], home=tmp_path)
        assert exc_info.value.code == 0
        assert not (notes / "tbd" / "inbox" / f"{_FIXED_TIMESTAMP}-001.md").exists()
        commit_cmd = [c["cmd"] for c in git_calls if "commit" in c["cmd"]][0]
        assert "chore: remove 1 tbd item" in " ".join(commit_cmd)

    def test_note_included_in_commit_message(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """`--note`指定時にcommit messageへ`(理由: <note>)`形式で追記されること。"""
        notes = _setup_tbd_env(tmp_path)
        _write_tbd_file(notes, f"{_FIXED_TIMESTAMP}-001.md")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))
        with pytest.raises(SystemExit) as exc_info:
            _cli.main(
                ["tbd-rm", f"{_FIXED_TIMESTAMP}-001.md", "--note", "誤投入"],
                home=tmp_path,
            )
        assert exc_info.value.code == 0
        commit_cmd = [c["cmd"] for c in git_calls if "commit" in c["cmd"]][0]
        assert "(理由: 誤投入)" in " ".join(commit_cmd)

    def test_multiple_files_removed_single_commit(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """複数ファイル指定時に1コミットへまとめて削除されること。"""
        notes = _setup_tbd_env(tmp_path)
        _write_tbd_file(notes, f"{_FIXED_TIMESTAMP}-001.md")
        _write_tbd_file(notes, f"{_FIXED_TIMESTAMP}-002.md")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))
        with pytest.raises(SystemExit) as exc_info:
            _cli.main(
                ["tbd-rm", f"{_FIXED_TIMESTAMP}-001.md", f"{_FIXED_TIMESTAMP}-002.md"],
                home=tmp_path,
            )
        assert exc_info.value.code == 0
        commit_cmds = [c["cmd"] for c in git_calls if "commit" in c["cmd"]]
        assert len(commit_cmds) == 1
        assert "chore: remove 2 tbd items" in " ".join(commit_cmds[0])

    def test_rejects_traversal(
        self,
        tmp_path: pathlib.Path,
    ) -> None:
        """パストラバーサル文字列は削除前検証で拒否されること。"""
        _setup_tbd_env(tmp_path)
        with pytest.raises(SystemExit):
            _cli.main(["tbd-rm", "../evil.md"], home=tmp_path)

    def test_missing_file_exits(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """指定ファイルがinbox配下に存在しないときexit 2で終了すること。"""
        _setup_tbd_env(tmp_path)
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))
        with pytest.raises(SystemExit):
            _cli.main(
                ["tbd-rm", f"{_FIXED_TIMESTAMP}-999.md"],
                home=tmp_path,
            )

    def test_partial_missing_file_prevents_any_removal(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """複数指定で一部欠損時に既存ファイルも削除されずcommitも発生しないこと。"""
        notes = _setup_tbd_env(tmp_path)
        existing = _write_tbd_file(notes, f"{_FIXED_TIMESTAMP}-001.md")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))
        with pytest.raises(SystemExit):
            _cli.main(
                [
                    "tbd-rm",
                    f"{_FIXED_TIMESTAMP}-001.md",
                    f"{_FIXED_TIMESTAMP}-999.md",
                ],
                home=tmp_path,
            )
        assert existing.exists()
        assert not [c["cmd"] for c in git_calls if "commit" in c["cmd"]]

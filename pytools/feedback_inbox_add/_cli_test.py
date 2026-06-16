"""pytools.feedback_inbox_add._cli のテスト。

同値分割と境界値分析で以下の観点を網羅する。
- フラグファイル不在 → exit 1とstderr案内
- ~/private-notes不在 → exit 1と手動clone案内
- 3章すべて含むmarkdown → 各章の項目数分のファイル生成
- 「提案無し」のみの章を含むmarkdown → 当該章をスキップ
- 単一章のみのmarkdown → 該当章のみ処理
- 全章「提案無し」の入力 → 0件で正常終了しgit操作も発生しない
- プロジェクトドキュメント章ありで--project-doc-repo未指定 → exit 1とstderr案内
- git操作のmock（subprocess.run差し替え）
"""

import datetime
import io
import pathlib
import subprocess
from typing import Any

import pytest

from pytools.feedback_inbox_add import _cli

# git呼び出し記録の型エイリアス
_GitCall = dict[str, Any]

# テスト用の固定タイムスタンプ
_FIXED_DT = datetime.datetime(2024, 1, 15, 10, 30, 0)
_FIXED_TIMESTAMP = "20240115-103000"
_FIXED_ISO = "2024-01-15T10:30:00"

_MARKDOWN_ALL_SECTIONS = """\
## プロジェクトドキュメント改善提案

- docs/architecture.md — 概要を追加する
- docs/api.md — サンプルを追加する

## pyfltr改善提案

- README.md — インストール手順を改善する

## agent-toolkit改善提案

- skills/coding-standards/SKILL.md — テスト方針を明確化する
- skills/writing-standards/SKILL.md — 用語表を追加する
"""

_MARKDOWN_WITH_NO_PROPOSAL = """\
## プロジェクトドキュメント改善提案

- 提案無し

## pyfltr改善提案

- README.md — インストール手順を改善する

## agent-toolkit改善提案

- 提案無し
"""

_MARKDOWN_SINGLE_SECTION = """\
## pyfltr改善提案

- README.md — インストール手順を改善する
"""

_MARKDOWN_ALL_NO_PROPOSAL = """\
## プロジェクトドキュメント改善提案

- 提案無し

## pyfltr改善提案

- 提案無し

## agent-toolkit改善提案

- 提案無し
"""

_MARKDOWN_WITH_PROJECT_DOC = """\
## プロジェクトドキュメント改善提案

- docs/guide/setup.md — 手順を改善する
"""


def _make_git_fake(calls: list[_GitCall]):
    """subprocess.run のfakeを返す。git呼び出し引数をcallsへ記録する。"""

    def fake(cmd: list[str], *args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        del args
        calls.append({"cmd": list(cmd), "kwargs": dict(kwargs)})
        return subprocess.CompletedProcess(cmd, returncode=0, stdout=b"", stderr=b"")

    return fake


def _setup_flag_and_notes(tmp_path: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path]:
    """フラグファイルとprivate-notesディレクトリを準備する。"""
    flag = tmp_path / ".config" / "agent-toolkit" / "feedback-inbox.enabled"
    flag.parent.mkdir(parents=True, exist_ok=True)
    flag.touch()
    notes = tmp_path / "private-notes"
    notes.mkdir()
    (notes / "feedback" / "inbox").mkdir(parents=True)
    return flag, notes


class TestFlagFileMissing:
    """フラグファイル不在時にexit 1とstderr案内を返すこと。"""

    def test_exits_with_error(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
    ):
        """フラグファイルが存在しない場合はexit 1でstderrに案内を出力する。"""
        monkeypatch.setattr(_cli.pathlib.Path, "home", lambda: tmp_path)
        monkeypatch.setattr("sys.stdin", io.StringIO(""))

        with pytest.raises(SystemExit) as exc_info:
            _cli.main([])

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "feedback-inbox機能が無効" in captured.err


class TestPrivateNotesMissing:
    """~/private-notes不在時にexit 1と手動clone案内を返すこと。"""

    def test_exits_with_clone_guide(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
    ):
        """private-notesが存在しない場合はexit 1でclone案内を出力する。"""
        flag = tmp_path / ".config" / "agent-toolkit" / "feedback-inbox.enabled"
        flag.parent.mkdir(parents=True, exist_ok=True)
        flag.touch()
        monkeypatch.setattr(_cli.pathlib.Path, "home", lambda: tmp_path)
        monkeypatch.setattr("sys.stdin", io.StringIO(""))

        with pytest.raises(SystemExit) as exc_info:
            _cli.main([])

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "clone" in captured.err


class TestAllSections:
    """3章すべて含むmarkdownで各章の項目数分のファイルが生成されること。"""

    def test_generates_files_for_all_sections(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ):
        """3章分5項目のmarkdown入力で5ファイルが生成されgit操作が発生する。"""
        _, notes = _setup_flag_and_notes(tmp_path)
        monkeypatch.setattr(_cli.pathlib.Path, "home", lambda: tmp_path)
        monkeypatch.setattr("sys.stdin", io.StringIO(_MARKDOWN_ALL_SECTIONS))
        monkeypatch.setattr(_cli.datetime, "datetime", _make_datetime_fake())

        git_calls: list[_GitCall] = []
        monkeypatch.setattr(_cli.subprocess, "run", _make_git_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            _cli.main(["--project-doc-repo", str(tmp_path / "myrepo")])

        assert exc_info.value.code == 0
        inbox = notes / "feedback" / "inbox"
        files = sorted(inbox.iterdir())
        assert len(files) == 5

        # git操作: pull → add → commit → push の順
        git_cmds = [c["cmd"] for c in git_calls]
        assert git_cmds[0] == ["git", "pull", "--ff-only"]
        assert git_cmds[1] == ["git", "add", str(inbox)]
        assert git_cmds[2] == ["git", "commit", "-m", "chore: add 5 feedback items"]
        assert git_cmds[3] == ["git", "push"]

        captured = capsys.readouterr()
        assert "5件投入" in captured.out


class TestSkipNoProposalSection:
    """「提案無し」のみの章をスキップすること。"""

    def test_skips_no_proposal_sections(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ):
        """「提案無し」章をスキップし有効な章の項目のみファイル生成する。"""
        _, notes = _setup_flag_and_notes(tmp_path)
        monkeypatch.setattr(_cli.pathlib.Path, "home", lambda: tmp_path)
        monkeypatch.setattr("sys.stdin", io.StringIO(_MARKDOWN_WITH_NO_PROPOSAL))
        monkeypatch.setattr(_cli.datetime, "datetime", _make_datetime_fake())

        git_calls: list[_GitCall] = []
        monkeypatch.setattr(_cli.subprocess, "run", _make_git_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            _cli.main([])

        assert exc_info.value.code == 0
        inbox = notes / "feedback" / "inbox"
        files = list(inbox.iterdir())
        # pyfltr章の1件のみ
        assert len(files) == 1
        content = files[0].read_text(encoding="utf-8")
        assert "source: pyfltr" in content

        captured = capsys.readouterr()
        assert "1件投入" in captured.out


class TestSingleSection:
    """単一章のみのmarkdownで該当章のみ処理すること。"""

    def test_processes_only_present_section(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ):
        """pyfltr章のみの入力で1ファイルが生成される。"""
        _, notes = _setup_flag_and_notes(tmp_path)
        monkeypatch.setattr(_cli.pathlib.Path, "home", lambda: tmp_path)
        monkeypatch.setattr("sys.stdin", io.StringIO(_MARKDOWN_SINGLE_SECTION))
        monkeypatch.setattr(_cli.datetime, "datetime", _make_datetime_fake())

        git_calls: list[_GitCall] = []
        monkeypatch.setattr(_cli.subprocess, "run", _make_git_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            _cli.main([])

        assert exc_info.value.code == 0
        inbox = notes / "feedback" / "inbox"
        files = list(inbox.iterdir())
        assert len(files) == 1
        content = files[0].read_text(encoding="utf-8")
        assert "source: pyfltr" in content
        assert f"target_repo: {tmp_path}/pyfltr" in content
        assert "target: README.md" in content

        captured = capsys.readouterr()
        assert "1件投入" in captured.out


class TestAllNoProposal:
    """全章「提案無し」の入力で0件正常終了しgit操作が発生しないこと。"""

    def test_exits_zero_without_git_ops(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ):
        """全章「提案無し」のときgit操作なしで正常終了する。"""
        _setup_flag_and_notes(tmp_path)
        monkeypatch.setattr(_cli.pathlib.Path, "home", lambda: tmp_path)
        monkeypatch.setattr("sys.stdin", io.StringIO(_MARKDOWN_ALL_NO_PROPOSAL))

        git_calls: list[_GitCall] = []
        monkeypatch.setattr(_cli.subprocess, "run", _make_git_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            _cli.main([])

        assert exc_info.value.code == 0
        assert not git_calls

        captured = capsys.readouterr()
        assert "処理対象なし" in captured.out


class TestProjectDocWithoutRepo:
    """プロジェクトドキュメント章ありで--project-doc-repo未指定の場合にexit 1とstderr案内を返すこと。"""

    def test_exits_with_error_without_repo_option(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ):
        """プロジェクトドキュメント章があり--project-doc-repoが未指定の場合はexit 1する。"""
        _setup_flag_and_notes(tmp_path)
        monkeypatch.setattr(_cli.pathlib.Path, "home", lambda: tmp_path)
        monkeypatch.setattr("sys.stdin", io.StringIO(_MARKDOWN_WITH_PROJECT_DOC))

        git_calls: list[_GitCall] = []
        monkeypatch.setattr(_cli.subprocess, "run", _make_git_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            _cli.main([])

        assert exc_info.value.code == 1
        assert not git_calls

        captured = capsys.readouterr()
        assert "--project-doc-repo" in captured.err


class TestGitOperationsMocked:
    """git操作がsubprocess.runを通じて正しく行われること。"""

    def test_git_call_sequence_and_args(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ):
        """git操作の呼び出し順序・引数・cwdを検証する。"""
        _, notes = _setup_flag_and_notes(tmp_path)
        monkeypatch.setattr(_cli.pathlib.Path, "home", lambda: tmp_path)
        monkeypatch.setattr("sys.stdin", io.StringIO(_MARKDOWN_SINGLE_SECTION))
        monkeypatch.setattr(_cli.datetime, "datetime", _make_datetime_fake())

        git_calls: list[_GitCall] = []
        monkeypatch.setattr(_cli.subprocess, "run", _make_git_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            _cli.main([])

        assert exc_info.value.code == 0

        # pull: cwdはprivate-notes
        pull_call = git_calls[0]
        assert pull_call["cmd"] == ["git", "pull", "--ff-only"]
        assert pull_call["kwargs"].get("cwd") == notes

        # add: cwdはprivate-notes
        add_call = git_calls[1]
        assert add_call["cmd"][0:2] == ["git", "add"]
        assert add_call["kwargs"].get("cwd") == notes

        # commit
        commit_call = git_calls[2]
        assert commit_call["cmd"][0:2] == ["git", "commit"]
        assert "chore: add 1 feedback items" in str(commit_call["cmd"])
        assert commit_call["kwargs"].get("cwd") == notes

        # push
        push_call = git_calls[3]
        assert push_call["cmd"] == ["git", "push"]
        assert push_call["kwargs"].get("cwd") == notes


def _make_datetime_fake() -> object:
    """datetime.datetime.now()が固定値を返すfakeクラスを返す。"""

    class _FakeDatetime:
        @staticmethod
        def now() -> datetime.datetime:
            return _FIXED_DT

    return _FakeDatetime

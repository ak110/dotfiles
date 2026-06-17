"""pytools.feedback_inbox_add._cli のテスト。

同値分割と境界値分析で以下の観点を網羅する。
- フラグファイル不在 → exit 1とstderr案内
- ~/private-notes不在 → exit 1と手動clone案内
- 位置引数未指定 → argparse由来のexit 2
- 単一メッセージ → 1ファイル生成・frontmatter検証・git操作順序検証
- 複数メッセージ → 件数分のファイル生成・連番付与・コミットメッセージ検証
- 改行を含むメッセージ → 改行がそのまま本文へ保存される
- ~プレフィックスのrepo_path → target_repoに絶対パスが書き込まれる
"""

import datetime
import pathlib
import subprocess
from collections.abc import Callable
from typing import Any

import pytest

from pytools.feedback_inbox_add import _cli

# git呼び出し記録の型エイリアス
_GitCall = dict[str, Any]

# テスト用の固定タイムスタンプ
_FIXED_DT = datetime.datetime(2024, 1, 15, 10, 30, 0)
_FIXED_TIMESTAMP = "20240115-103000"
_FIXED_ISO = "2024-01-15T10:30:00"


def _make_git_fake(calls: list[_GitCall]) -> Callable[..., subprocess.CompletedProcess[bytes]]:
    """subprocess.run のfakeを返す。git呼び出し引数をcallsへ記録する。"""

    def fake(cmd: list[str], *args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        del args
        calls.append({"cmd": list(cmd), "kwargs": dict(kwargs)})
        return subprocess.CompletedProcess(cmd, returncode=0, stdout=b"", stderr=b"")

    return fake


def _setup_flag_and_notes(tmp_path: pathlib.Path) -> pathlib.Path:
    """フラグファイルとprivate-notesディレクトリを準備する。"""
    flag = tmp_path / ".config" / "agent-toolkit" / "feedback-inbox.enabled"
    flag.parent.mkdir(parents=True, exist_ok=True)
    flag.touch()
    notes = tmp_path / "private-notes"
    notes.mkdir()
    (notes / "feedback" / "inbox").mkdir(parents=True)
    return notes


class TestFlagFileMissing:
    """フラグファイル不在時にexit 1とstderr案内を返すこと。"""

    def test_exits_with_error(self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
        """フラグファイルが存在しない場合はexit 1でstderrに案内を出力する。"""
        with pytest.raises(SystemExit) as exc_info:
            _cli.main([str(tmp_path / "myrepo"), "dummy message"], home=tmp_path)

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "feedback-inbox機能が無効" in captured.err


class TestPrivateNotesMissing:
    """~/private-notes不在時にexit 1と手動clone案内を返すこと。"""

    def test_exits_with_clone_guide(self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
        """private-notesが存在しない場合はexit 1でclone案内を出力する。"""
        flag = tmp_path / ".config" / "agent-toolkit" / "feedback-inbox.enabled"
        flag.parent.mkdir(parents=True, exist_ok=True)
        flag.touch()

        with pytest.raises(SystemExit) as exc_info:
            _cli.main([str(tmp_path / "myrepo"), "dummy message"], home=tmp_path)

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "clone" in captured.err


class TestMissingMessages:
    """位置引数が未指定の場合にargparse由来のSystemExit（exit 2）が発生すること。"""

    def test_exits_with_usage_error(self, tmp_path: pathlib.Path) -> None:
        """repo_pathのみ指定でmessagesが未指定の場合はexit 2でSystemExitが発生する。"""
        with pytest.raises(SystemExit) as exc_info:
            _cli.main([str(tmp_path / "myrepo")])

        assert exc_info.value.code == 2


class TestSingleMessage:
    """単一メッセージで1ファイル生成・frontmatter検証・git操作順序検証を行うこと。"""

    def test_single_message_generates_one_file(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """単一メッセージで1ファイルが生成され、frontmatterとgit操作順序が正しいこと。"""
        notes = _setup_flag_and_notes(tmp_path)

        git_calls: list[_GitCall] = []
        monkeypatch.setattr(_cli.subprocess, "run", _make_git_fake(git_calls))

        repo_path = str(tmp_path / "myrepo")
        message = "テストメッセージ"

        with pytest.raises(SystemExit) as exc_info:
            _cli.main([repo_path, message], home=tmp_path, now=_FIXED_DT)

        assert exc_info.value.code == 0

        # 1ファイル生成
        inbox = notes / "feedback" / "inbox"
        files = sorted(inbox.iterdir())
        assert len(files) == 1

        # frontmatterにcreatedとtarget_repoのみを含む
        content = files[0].read_text(encoding="utf-8")
        assert f"created: {_FIXED_ISO}" in content
        assert f"target_repo: {tmp_path}/myrepo" in content
        assert "source:" not in content
        assert "target:" not in content

        # 本文がmessage文字列と一致する
        # content形式: "---\n...\n---\n\n{message}\n"
        body = content.split("---\n\n", 1)[1]
        assert body == message + "\n"

        # git操作: pull → add → commit → push の順とcwd検証
        git_cmds = [c["cmd"] for c in git_calls]
        assert git_cmds[0] == ["git", "pull", "--ff-only"]
        assert git_cmds[1] == ["git", "add", str(inbox)]
        assert git_cmds[2] == ["git", "commit", "-m", "chore: add 1 feedback item"]
        assert git_cmds[3] == ["git", "push"]

        for call in git_calls:
            assert call["kwargs"].get("cwd") == notes

        captured = capsys.readouterr()
        assert "1件投入" in captured.out
        assert "inbox: 計1件" in captured.out


class TestMultipleMessages:
    """2件以上のメッセージで件数分のファイルが生成され連番とコミットメッセージが正しいこと。"""

    def test_multiple_messages_generate_files_with_sequence(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """2件のメッセージで2ファイルが生成され、連番001・002が付与され、コミットメッセージが正しいこと。"""
        notes = _setup_flag_and_notes(tmp_path)

        git_calls: list[_GitCall] = []
        monkeypatch.setattr(_cli.subprocess, "run", _make_git_fake(git_calls))

        repo_path = str(tmp_path / "myrepo")

        with pytest.raises(SystemExit) as exc_info:
            _cli.main([repo_path, "メッセージ1", "メッセージ2"], home=tmp_path, now=_FIXED_DT)

        assert exc_info.value.code == 0

        inbox = notes / "feedback" / "inbox"
        files = sorted(inbox.iterdir())
        assert len(files) == 2

        # 連番001・002の順で付与される
        assert files[0].name == f"{_FIXED_TIMESTAMP}-001.md"
        assert files[1].name == f"{_FIXED_TIMESTAMP}-002.md"

        # コミットメッセージが正しい
        commit_cmd = [c["cmd"] for c in git_calls if "commit" in c["cmd"]][0]
        assert "chore: add 2 feedback items" in commit_cmd

        captured = capsys.readouterr()
        assert "2件投入" in captured.out
        assert "inbox: 計2件" in captured.out


class TestInboxCount:
    """既存inboxファイルがある状態で投入した際の件数表示の検証。"""

    def test_inbox_count_includes_existing_files(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """既存3件＋新規投入2件の状態でinbox全件数として5件を出力する。"""
        notes = _setup_flag_and_notes(tmp_path)
        inbox = notes / "feedback" / "inbox"
        for i in range(3):
            (inbox / f"existing-{i:03d}.md").write_text("dummy\n", encoding="utf-8")

        monkeypatch.setattr(_cli.subprocess, "run", _make_git_fake([]))

        repo_path = str(tmp_path / "myrepo")

        with pytest.raises(SystemExit) as exc_info:
            _cli.main([repo_path, "メッセージ1", "メッセージ2"], home=tmp_path, now=_FIXED_DT)

        assert exc_info.value.code == 0

        captured = capsys.readouterr()
        assert "inbox: 計5件" in captured.out


class TestMultilineMessage:
    """改行を含むメッセージで改行がそのまま本文に保存されること。"""

    def test_multiline_message_preserves_newlines(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """改行を含むメッセージが本文にそのまま保存される。"""
        notes = _setup_flag_and_notes(tmp_path)
        monkeypatch.setattr(_cli.subprocess, "run", _make_git_fake([]))

        repo_path = str(tmp_path / "myrepo")
        message = "1行目\n2行目\n3行目"

        with pytest.raises(SystemExit) as exc_info:
            _cli.main([repo_path, message], home=tmp_path, now=_FIXED_DT)

        assert exc_info.value.code == 0

        inbox = notes / "feedback" / "inbox"
        files = list(inbox.iterdir())
        assert len(files) == 1

        content = files[0].read_text(encoding="utf-8")
        body = content.split("---\n\n", 1)[1]
        assert body == message + "\n"


class TestRepoPathExpansion:
    """~プレフィックスのrepo_pathでtarget_repoに絶対パスが書き込まれること。"""

    def test_tilde_repo_path_is_expanded(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """~展開後の絶対パスがtarget_repoへ書き込まれる。"""
        notes = _setup_flag_and_notes(tmp_path)
        # expanduser()はHOME環境変数を参照するため合わせて設定する
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setattr(_cli.subprocess, "run", _make_git_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            _cli.main(["~/myrepo", "テストメッセージ"], home=tmp_path, now=_FIXED_DT)

        assert exc_info.value.code == 0

        inbox = notes / "feedback" / "inbox"
        files = list(inbox.iterdir())
        assert len(files) == 1

        content = files[0].read_text(encoding="utf-8")
        # ~が展開されてtmp_path/myrepoの絶対パスになる
        assert f"target_repo: {tmp_path}/myrepo" in content

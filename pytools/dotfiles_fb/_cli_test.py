"""pytools.dotfiles_fb._cli のテスト。

同値分割と境界値分析で各サブコマンドの観点を網羅する。
add/list/adopt/reject/rm/edit/process-loopなど既存サブコマンドの単体テストを集約する。
commit/enable/disable/--source等の拡張機能テストは`_cli_extras_test.py`に分離する。
"""

import datetime
import pathlib
import subprocess
from collections.abc import Callable
from typing import Any

import pytest

from pytools.dotfiles_fb import _cli

_GitCall = dict[str, Any]

_FIXED_DT = datetime.datetime(2024, 1, 15, 10, 30, 0)
_FIXED_TIMESTAMP = _FIXED_DT.strftime("%Y%m%d-%H%M%S")
_FIXED_ISO = _FIXED_DT.isoformat()


def _make_subprocess_fake(
    calls: list[_GitCall],
) -> Callable[..., subprocess.CompletedProcess[Any]]:
    """subprocess.runのfakeを返す。呼び出し引数をcallsへ記録する。

    `text=True`が指定された場合は`stdout`/`stderr`を空文字列で返し、それ以外は空バイト列で返す。
    """

    def fake(cmd: list[str], *args: object, **kwargs: object) -> subprocess.CompletedProcess[Any]:
        del args
        calls.append({"cmd": list(cmd), "kwargs": dict(kwargs)})
        empty: Any = "" if kwargs.get("text") else b""
        return subprocess.CompletedProcess(cmd, returncode=0, stdout=empty, stderr=empty)

    return fake


def _setup_flag_and_notes(tmp_path: pathlib.Path) -> pathlib.Path:
    """フラグファイルとprivate-notesディレクトリを準備する。"""
    flag = tmp_path / ".config" / "agent-toolkit" / "feedback-inbox.enabled"
    flag.parent.mkdir(parents=True, exist_ok=True)
    flag.touch()
    notes = tmp_path / "private-notes"
    notes.mkdir()
    (notes / "feedback").mkdir(parents=True)
    return notes


def _write_feedback_file(
    notes: pathlib.Path,
    filename: str,
    target_repo: str = "/repo/foo",
    body: str = "テスト本文",
) -> pathlib.Path:
    """feedback配下に1ファイルを書き込み、絶対パスを返す。"""
    feedback_dir = notes / "feedback"
    feedback_dir.mkdir(parents=True, exist_ok=True)
    path = feedback_dir / filename
    path.write_text(
        f"---\ncreated: {_FIXED_ISO}\ntarget_repo: {target_repo}\n---\n\n{body}\n",
        encoding="utf-8",
    )
    return path


class TestFlagFileMissing:
    """フラグファイル不在時にexit 1とstderr案内を返すこと。"""

    def test_exits_with_error(self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
        """フラグファイルが存在しない場合はexit 1でstderrに案内を出力する。"""
        with pytest.raises(SystemExit) as exc_info:
            _cli.main(["add", str(tmp_path / "myrepo"), "dummy message"], home=tmp_path)

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
            _cli.main(["add", str(tmp_path / "myrepo"), "dummy message"], home=tmp_path)

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "クローン" in captured.err


class TestNoSubcommand:
    """サブコマンド未指定時にargparse由来のexit 2が発生すること。"""

    def test_exits_with_usage_error(self) -> None:
        """サブコマンド未指定の場合はexit 2でSystemExitが発生する。"""
        with pytest.raises(SystemExit) as exc_info:
            _cli.main([])

        assert exc_info.value.code == 2


class TestAddSingleMessage:
    """addサブコマンド: 単一メッセージで1ファイル生成とgit操作順序を検証する。"""

    def test_single_message_generates_one_file(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """単一メッセージで1ファイルが生成され、frontmatterとgit操作順序が正しいこと。"""
        notes = _setup_flag_and_notes(tmp_path)

        git_calls: list[_GitCall] = []
        monkeypatch.setattr(_cli.subprocess, "run", _make_subprocess_fake(git_calls))

        repo_path = str(tmp_path / "myrepo")
        message = "テストメッセージ"

        with pytest.raises(SystemExit) as exc_info:
            _cli.main(["add", repo_path, message], home=tmp_path, now=_FIXED_DT)

        assert exc_info.value.code == 0

        feedback_dir = notes / "feedback"
        files = sorted(feedback_dir.iterdir())
        assert len(files) == 1

        content = files[0].read_text(encoding="utf-8")
        assert f"created: {_FIXED_ISO}" in content
        assert f"target_repo: {tmp_path}/myrepo" in content

        body = content.split("---\n\n", 1)[1]
        assert body == message + "\n"

        git_cmds = [c["cmd"] for c in git_calls]
        assert git_cmds[0] == ["git", "pull", "--ff-only"]
        assert git_cmds[1] == ["git", "add", "feedback"]
        assert git_cmds[2] == ["git", "commit", "-m", "chore: add 1 feedback item"]
        assert git_cmds[3] == ["git", "push"]
        for call in git_calls:
            assert call["kwargs"].get("cwd") == notes

        captured = capsys.readouterr()
        assert "1件投入:\n" in captured.out
        assert f"  ~/private-notes/feedback/{files[0].name}\n" in captured.out
        assert "inbox: 計1件" in captured.out


class TestAddMultipleMessages:
    """addサブコマンド: 2件以上のメッセージで連番と件数コミットメッセージを検証する。"""

    def test_multiple_messages_generate_files_with_sequence(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """2件のメッセージで連番001・002の付与とコミットメッセージを検証する。"""
        notes = _setup_flag_and_notes(tmp_path)

        git_calls: list[_GitCall] = []
        monkeypatch.setattr(_cli.subprocess, "run", _make_subprocess_fake(git_calls))

        repo_path = str(tmp_path / "myrepo")

        with pytest.raises(SystemExit) as exc_info:
            _cli.main(["add", repo_path, "メッセージ1", "メッセージ2"], home=tmp_path, now=_FIXED_DT)

        assert exc_info.value.code == 0

        feedback_dir = notes / "feedback"
        files = sorted(feedback_dir.iterdir())
        assert len(files) == 2
        assert files[0].name == f"{_FIXED_TIMESTAMP}-001.md"
        assert files[1].name == f"{_FIXED_TIMESTAMP}-002.md"

        commit_cmd = [c["cmd"] for c in git_calls if "commit" in c["cmd"]][0]
        assert "chore: add 2 feedback items" in commit_cmd

        captured = capsys.readouterr()
        assert "2件投入:\n" in captured.out
        assert f"  ~/private-notes/feedback/{files[0].name}\n" in captured.out
        assert f"  ~/private-notes/feedback/{files[1].name}\n" in captured.out
        assert "inbox: 計2件" in captured.out


class TestAddRepoPathExpansion:
    """addサブコマンド: ~プレフィックスのrepo_pathが絶対パスへ展開されること。"""

    def test_tilde_repo_path_is_expanded(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """~展開後の絶対パスがtarget_repoへ書き込まれる。"""
        notes = _setup_flag_and_notes(tmp_path)
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setattr(_cli.subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            _cli.main(["add", "~/myrepo", "テストメッセージ"], home=tmp_path, now=_FIXED_DT)

        assert exc_info.value.code == 0

        inbox = notes / "feedback"
        files = list(inbox.iterdir())
        assert len(files) == 1
        content = files[0].read_text(encoding="utf-8")
        assert f"target_repo: {tmp_path}/myrepo" in content


class TestListEmpty:
    """listサブコマンド: inbox空の場合は何も出力しない。"""

    def test_empty_inbox(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """inbox空時は標準出力が空であること。"""
        _setup_flag_and_notes(tmp_path)
        monkeypatch.setattr(_cli.subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            _cli.main(["list"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert captured.out == ""


class TestListSingle:
    """listサブコマンド: 1件のフィードバックを出力する。"""

    def test_single_entry(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """1件のフィードバックがtarget_repoグループ・ファイル名・本文の順で出力されること。"""
        notes = _setup_flag_and_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md", target_repo="/repo/foo", body="本文1")
        monkeypatch.setattr(_cli.subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            _cli.main(["list"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "## target_repo: /repo/foo" in captured.out
        assert "### fb-001.md" in captured.out
        assert "本文1" in captured.out


class TestListMalformedFrontmatter:
    """listサブコマンド: 異常frontmatterは`(unknown)`グループへ振り分けられる。"""

    @pytest.mark.parametrize(
        ("content", "label"),
        [
            ("本文のみ\n", "frontmatterなし"),
            ("---\ncreated: 2024\n本文\n", "閉じ区切りなし"),
            ("---\ncreated: 2024\n---\n\n本文\n", "target_repo欠落"),
        ],
    )
    def test_malformed_frontmatter_falls_back_to_unknown(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
        content: str,
        label: str,
    ) -> None:
        """異常frontmatter形式は`(unknown)`グループとして出力される。"""
        del label  # parametrize idのみ
        notes = _setup_flag_and_notes(tmp_path)
        (notes / "feedback" / "malformed.md").write_text(content, encoding="utf-8")
        monkeypatch.setattr(_cli.subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            _cli.main(["list"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "## target_repo: (unknown)" in captured.out
        assert "### malformed.md" in captured.out


class TestListMultipleRepos:
    """listサブコマンド: 複数target_repo混在でグループ化される。"""

    def test_multiple_repos_grouped(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """target_repoが異なる複数のフィードバックがリポジトリ別にグループ化される。"""
        notes = _setup_flag_and_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md", target_repo="/repo/foo")
        _write_feedback_file(notes, "fb-002.md", target_repo="/repo/bar")
        monkeypatch.setattr(_cli.subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            _cli.main(["list"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "## target_repo: /repo/foo" in captured.out
        assert "## target_repo: /repo/bar" in captured.out


class TestListTargetRepoFilter:
    """listサブコマンド: --target-repo指定で該当グループのみ出力する。"""

    def test_filter_matches_single_group(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """複数target_repo混在でも--target-repo指定値と一致するグループのみ出力される。"""
        notes = _setup_flag_and_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md", target_repo="/repo/foo")
        _write_feedback_file(notes, "fb-002.md", target_repo="/repo/bar")
        monkeypatch.setattr(_cli.subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            _cli.main(["list", "--target-repo=/repo/foo"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "## target_repo: /repo/foo" in captured.out
        assert "/repo/bar" not in captured.out

    def test_filter_expands_tilde(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """~プレフィックス指定がtmp_path配下の絶対パスへ展開され、対応するエントリが出力される。"""
        notes = _setup_flag_and_notes(tmp_path)
        target = str((tmp_path / "myrepo").resolve())
        _write_feedback_file(notes, "fb-001.md", target_repo=target)
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setattr(_cli.subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            _cli.main(["list", "--target-repo=~/myrepo"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert f"## target_repo: {target}" in captured.out
        assert "### fb-001.md" in captured.out

    def test_filter_no_match_outputs_nothing(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """一致するエントリが存在しない場合、標準出力は空になる。"""
        notes = _setup_flag_and_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md", target_repo="/repo/foo")
        monkeypatch.setattr(_cli.subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            _cli.main(["list", "--target-repo=/repo/nomatch"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert captured.out == ""


class TestAdoptSingle:
    """adoptサブコマンド: 1件指定でinbox削除とコミットを行う。"""

    def test_single_file_adopted(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """1件のadopt実行でinboxから削除されコミットメッセージが正しいこと。"""
        notes = _setup_flag_and_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(_cli.subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            _cli.main(["adopt", "fb-001.md"], home=tmp_path)

        assert exc_info.value.code == 0
        assert not (notes / "feedback" / "fb-001.md").exists()

        commit_cmd = [c["cmd"] for c in git_calls if "commit" in c["cmd"]][0]
        assert "chore: process 1 feedback item (adopted)" in commit_cmd


class TestAdoptMultiple:
    """adoptサブコマンド: 複数件指定で単一コミットへまとめる。"""

    def test_multiple_files_adopted_single_commit(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """3件のadoptで全件削除と単一コミットが行われること。"""
        notes = _setup_flag_and_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md")
        _write_feedback_file(notes, "fb-002.md")
        _write_feedback_file(notes, "fb-003.md")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(_cli.subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            _cli.main(["adopt", "fb-001.md", "fb-002.md", "fb-003.md"], home=tmp_path)

        assert exc_info.value.code == 0
        inbox = notes / "feedback"
        assert not (inbox / "fb-001.md").exists()
        assert not (inbox / "fb-002.md").exists()
        assert not (inbox / "fb-003.md").exists()

        commit_cmds = [c["cmd"] for c in git_calls if "commit" in c["cmd"]]
        assert len(commit_cmds) == 1
        assert "chore: process 3 feedback items (adopted)" in commit_cmds[0]


class TestAdoptMissing:
    """adoptサブコマンド: 存在しないファイル指定でexit 2となる。"""

    def test_missing_file_exits(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """inboxに存在しないファイル名指定でexit 2と案内が出力される。"""
        _setup_flag_and_notes(tmp_path)
        monkeypatch.setattr(_cli.subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            _cli.main(["adopt", "nonexistent.md"], home=tmp_path)

        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        assert "inboxに存在しません" in captured.err


class TestRejectDeletes:
    """rejectサブコマンド: ファイルをinboxから削除する。"""

    def test_single_file_rejected(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """rejectでファイルがinboxから削除されコミット件名が正しいこと。"""
        notes = _setup_flag_and_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(_cli.subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            _cli.main(["reject", "fb-001.md"], home=tmp_path)

        assert exc_info.value.code == 0
        assert not (notes / "feedback" / "fb-001.md").exists()
        assert not (notes / "feedback" / "rejected").exists()
        commit_cmd = [c["cmd"] for c in git_calls if "commit" in c["cmd"]][0]
        assert "chore: process 1 feedback item (rejected)" in commit_cmd


class TestRejectMultiple:
    """rejectサブコマンド: 複数件指定で単一コミットへまとめる。"""

    def test_multiple_files_rejected_single_commit(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """2件のrejectで両方削除と単一コミットが行われること。"""
        notes = _setup_flag_and_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md")
        _write_feedback_file(notes, "fb-002.md")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(_cli.subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            _cli.main(["reject", "fb-001.md", "fb-002.md"], home=tmp_path)

        assert exc_info.value.code == 0
        commit_cmds = [c["cmd"] for c in git_calls if "commit" in c["cmd"]]
        assert len(commit_cmds) == 1
        assert "chore: process 2 feedback items (rejected)" in commit_cmds[0]


class TestRmSingle:
    """rmサブコマンド: 単純削除とコミット件名を検証する。"""

    def test_single_file_removed(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """rmで対象ファイルが削除されコミット件名が正しいこと。"""
        notes = _setup_flag_and_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(_cli.subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            _cli.main(["rm", "fb-001.md"], home=tmp_path)

        assert exc_info.value.code == 0
        assert not (notes / "feedback" / "fb-001.md").exists()
        commit_cmd = [c["cmd"] for c in git_calls if "commit" in c["cmd"]][0]
        assert "chore: remove 1 feedback item" in commit_cmd


class TestRmMultiple:
    """rmサブコマンド: 複数件指定で単一コミットへまとめる。"""

    def test_multiple_files_removed_single_commit(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """2件のrmで両方削除と単一コミットが行われること。"""
        notes = _setup_flag_and_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md")
        _write_feedback_file(notes, "fb-002.md")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(_cli.subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            _cli.main(["rm", "fb-001.md", "fb-002.md"], home=tmp_path)

        assert exc_info.value.code == 0
        commit_cmds = [c["cmd"] for c in git_calls if "commit" in c["cmd"]]
        assert len(commit_cmds) == 1
        assert "chore: remove 2 feedback items" in commit_cmds[0]


class TestEditNoEditor:
    """editサブコマンド: $EDITOR未設定でexit 1となる。"""

    def test_no_editor_exits(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """$EDITORが未設定の場合はexit 1と案内が出力される。"""
        notes = _setup_flag_and_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md")
        monkeypatch.delenv("EDITOR", raising=False)

        with pytest.raises(SystemExit) as exc_info:
            _cli.main(["edit", "fb-001.md"], home=tmp_path)

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "EDITOR" in captured.err


class TestEditWithChanges:
    """editサブコマンド: 編集後差分ありでcommit・push実行。"""

    def test_edit_with_changes_commits(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """編集後にファイル差分があればコミット・pushが実行される。"""
        notes = _setup_flag_and_notes(tmp_path)
        path = _write_feedback_file(notes, "fb-001.md", body="編集前")
        monkeypatch.setenv("EDITOR", "fake-editor")

        git_calls: list[_GitCall] = []

        def fake_run(cmd: list[str], *_args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
            if cmd[0] == "fake-editor":
                path.write_text("編集後\n", encoding="utf-8")
                return subprocess.CompletedProcess(cmd, returncode=0, stdout=b"", stderr=b"")
            git_calls.append({"cmd": list(cmd), "kwargs": dict(kwargs)})
            return subprocess.CompletedProcess(cmd, returncode=0, stdout=b"", stderr=b"")

        monkeypatch.setattr(_cli.subprocess, "run", fake_run)

        with pytest.raises(SystemExit) as exc_info:
            _cli.main(["edit", "fb-001.md"], home=tmp_path)

        assert exc_info.value.code == 0
        commit_cmd = [c["cmd"] for c in git_calls if "commit" in c["cmd"]][0]
        assert "chore: edit feedback item" in commit_cmd


class TestEditNoChanges:
    """editサブコマンド: 差分なしでcommitせず終了。"""

    def test_edit_no_changes_skips_commit(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """編集後にファイル差分がなければコミットされず案内のみ出力される。"""
        notes = _setup_flag_and_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md", body="本文")
        monkeypatch.setenv("EDITOR", "fake-editor")

        git_calls: list[_GitCall] = []

        def fake_run(cmd: list[str], *_args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
            if cmd[0] == "fake-editor":
                return subprocess.CompletedProcess(cmd, returncode=0, stdout=b"", stderr=b"")
            git_calls.append({"cmd": list(cmd), "kwargs": dict(kwargs)})
            return subprocess.CompletedProcess(cmd, returncode=0, stdout=b"", stderr=b"")

        monkeypatch.setattr(_cli.subprocess, "run", fake_run)

        with pytest.raises(SystemExit) as exc_info:
            _cli.main(["edit", "fb-001.md"], home=tmp_path)

        assert exc_info.value.code == 0
        commit_cmds = [c["cmd"] for c in git_calls if "commit" in c["cmd"]]
        assert commit_cmds == []
        captured = capsys.readouterr()
        assert "差分なし" in captured.out


class TestPathTraversalRejection:
    """パストラバーサル系の不正引数は早期に拒否されること。"""

    @pytest.mark.parametrize(
        "bad",
        [
            "../escape.md",
            "subdir/file.md",
            "/abs/path.md",
            "..\\windows.md",
            "..",
            ".",
            "",
        ],
    )
    def test_rejects_bad_filenames(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
        bad: str,
    ) -> None:
        """不正なファイル名引数はexit 2でstderr案内を出力する。"""
        _setup_flag_and_notes(tmp_path)
        monkeypatch.setattr(_cli.subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            _cli.main(["adopt", bad], home=tmp_path)

        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        assert "不正なファイル名" in captured.err or "基準ディレクトリ外" in captured.err


class TestProcessLoopEmptyInbox:
    """process-loopサブコマンド: 対象リポのinbox空時はclaude未起動でexit 0。"""

    def test_empty_inbox_skips_claude(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """対象リポのinboxが最初から空ならclaudeが一度も呼ばれない。"""
        _setup_flag_and_notes(tmp_path)
        calls: list[_GitCall] = []
        monkeypatch.setattr(_cli.subprocess, "run", _make_subprocess_fake(calls))

        with pytest.raises(SystemExit) as exc_info:
            _cli.main(["process-loop", "--target-repo=/repo/foo"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "対象リポのinboxは空です" in captured.out
        claude_calls = [c for c in calls if c["cmd"][:1] == ["claude"]]
        assert claude_calls == []


class TestProcessLoopSingleIteration:
    """process-loopサブコマンド: claude起動で対象リポinboxが空になれば1回で終了。"""

    def test_single_iteration_when_inbox_drains(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """claudeのfakeが対象リポinboxを空にすると1回の反復でループを抜ける。"""
        notes = _setup_flag_and_notes(tmp_path)
        inbox_path = _write_feedback_file(notes, "fb-001.md", target_repo="/repo/foo")
        claude_calls: list[list[str]] = []

        def fake_run(cmd: list[str], *_args: object, **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
            if cmd[:1] == ["claude"]:
                claude_calls.append(list(cmd))
                inbox_path.unlink()
            return subprocess.CompletedProcess(cmd, returncode=0, stdout=b"", stderr=b"")

        monkeypatch.setattr(_cli.subprocess, "run", fake_run)

        with pytest.raises(SystemExit) as exc_info:
            _cli.main(["process-loop", "--target-repo=/repo/foo"], home=tmp_path)

        assert exc_info.value.code == 0
        assert claude_calls == [["claude", "--permission-mode=auto", "/process-feedbacks", "/repo/foo"]]
        captured = capsys.readouterr()
        assert "[反復 1] 対象リポinbox残1件" in captured.out
        assert "対象リポのinboxが空になりました（1回実行" in captured.out


class TestProcessLoopMaxIterations:
    """process-loopサブコマンド: --max-iterationsで反復上限を強制。"""

    def test_max_iterations_caps_loop(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """claudeが対象リポinboxを空にしなくても上限回数で停止する。"""
        notes = _setup_flag_and_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md", target_repo="/repo/foo")
        claude_calls: list[list[str]] = []

        def fake_run(cmd: list[str], *_args: object, **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
            if cmd[:1] == ["claude"]:
                claude_calls.append(list(cmd))
            return subprocess.CompletedProcess(cmd, returncode=0, stdout=b"", stderr=b"")

        monkeypatch.setattr(_cli.subprocess, "run", fake_run)

        with pytest.raises(SystemExit) as exc_info:
            _cli.main(["process-loop", "--max-iterations=2", "--target-repo=/repo/foo"], home=tmp_path)

        assert exc_info.value.code == 0
        assert len(claude_calls) == 2
        captured = capsys.readouterr()
        assert "反復上限2回に達しました（対象リポinbox残1件）" in captured.out


class TestProcessLoopClaudeFailure:
    """process-loopサブコマンド: claude非0終了時に同じexit codeで中断する。"""

    def test_claude_failure_exits_with_returncode(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """claudeのfakeが非0を返すとprocess-loopは同じexit codeで停止する。"""
        notes = _setup_flag_and_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md", target_repo="/repo/foo")

        def fake_run(cmd: list[str], *_args: object, **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
            if cmd[:1] == ["claude"]:
                return subprocess.CompletedProcess(cmd, returncode=42, stdout=b"", stderr=b"")
            return subprocess.CompletedProcess(cmd, returncode=0, stdout=b"", stderr=b"")

        monkeypatch.setattr(_cli.subprocess, "run", fake_run)

        with pytest.raises(SystemExit) as exc_info:
            _cli.main(["process-loop", "--target-repo=/repo/foo"], home=tmp_path)

        assert exc_info.value.code == 42
        captured = capsys.readouterr()
        assert "claudeがexit code 42で終了しました" in captured.err


class TestProcessLoopTargetRepoFilter:
    """process-loopサブコマンド: --target-repoで対象外フィードバックは件数に含めない。"""

    def test_target_repo_filters_count(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """対象外リポのフィードバックは件数判定から除外され、claudeは起動しない。"""
        notes = _setup_flag_and_notes(tmp_path)
        _write_feedback_file(notes, "fb-other.md", target_repo="/repo/other")
        calls: list[_GitCall] = []
        monkeypatch.setattr(_cli.subprocess, "run", _make_subprocess_fake(calls))

        with pytest.raises(SystemExit) as exc_info:
            _cli.main(["process-loop", "--target-repo=/repo/foo"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "対象リポのinboxは空です" in captured.out
        claude_calls = [c for c in calls if c["cmd"][:1] == ["claude"]]
        assert claude_calls == []


class TestProcessLoopDefaultUsesGitToplevel:
    """process-loopサブコマンド: --target-repo未指定時はgit rev-parseで現リポを取得する。"""

    def test_default_target_repo_resolved_via_git(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """--target-repo未指定なら`git rev-parse --show-toplevel`を呼び結果をclaude引数に渡す。"""
        notes = _setup_flag_and_notes(tmp_path)
        inbox_path = _write_feedback_file(notes, "fb-001.md", target_repo="/repo/auto")
        claude_calls: list[list[str]] = []

        def fake_run(cmd: list[str], *_args: object, **kwargs: object) -> subprocess.CompletedProcess[Any]:
            if cmd == ["git", "rev-parse", "--show-toplevel"]:
                stdout: Any = "/repo/auto\n" if kwargs.get("text") else b"/repo/auto\n"
                stderr: Any = "" if kwargs.get("text") else b""
                return subprocess.CompletedProcess(cmd, returncode=0, stdout=stdout, stderr=stderr)
            if cmd[:1] == ["claude"]:
                claude_calls.append(list(cmd))
                inbox_path.unlink()
            return subprocess.CompletedProcess(cmd, returncode=0, stdout=b"", stderr=b"")

        monkeypatch.setattr(_cli.subprocess, "run", fake_run)

        with pytest.raises(SystemExit) as exc_info:
            _cli.main(["process-loop"], home=tmp_path)

        assert exc_info.value.code == 0
        assert claude_calls == [["claude", "--permission-mode=auto", "/process-feedbacks", "/repo/auto"]]

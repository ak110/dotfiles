"""pytools.dotfiles_fb._cli の拡張サブコマンド・オプションのテスト。

`add --source`・`list`のpull実行・`commit`・`enable`・`disable`の単体テストを集約する。
既存サブコマンドのテストは`_cli_test.py`に分離する。
共通ヘルパーは`_cli_test.py`から再利用する。
"""

import pathlib
import subprocess
from typing import Any

import pytest

from pytools.dotfiles_fb import _cli
from pytools.dotfiles_fb._cli_test import (
    _FIXED_DT,
    _GitCall,
    _make_subprocess_fake,
    _setup_flag_and_notes,
)


class TestAddSourceOption:
    """addサブコマンド: --source指定時にfrontmatterへsource行を記録する。"""

    def test_source_recorded_when_given(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """--source=session-review指定時、frontmatterにsource: session-reviewが含まれる。"""
        notes = _setup_flag_and_notes(tmp_path)
        monkeypatch.setattr(_cli.subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            _cli.main(
                ["add", "--source=session-review", str(tmp_path / "myrepo"), "メッセージ"],
                home=tmp_path,
                now=_FIXED_DT,
            )

        assert exc_info.value.code == 0
        content = next((notes / "feedback" / "inbox").iterdir()).read_text(encoding="utf-8")
        assert "source: session-review" in content

    def test_source_absent_when_not_given(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """--source未指定時、frontmatterにsource行が含まれない。"""
        notes = _setup_flag_and_notes(tmp_path)
        monkeypatch.setattr(_cli.subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            _cli.main(["add", str(tmp_path / "myrepo"), "メッセージ"], home=tmp_path, now=_FIXED_DT)

        assert exc_info.value.code == 0
        content = next((notes / "feedback" / "inbox").iterdir()).read_text(encoding="utf-8")
        assert "source:" not in content


class TestListPullsBeforeRead:
    """listサブコマンド: 出力前にgit pull --ff-onlyを実行する。"""

    def test_list_pulls_before_reading(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """list実行時に最初のgit呼び出しがpullであること。"""
        _setup_flag_and_notes(tmp_path)
        calls: list[_GitCall] = []
        monkeypatch.setattr(_cli.subprocess, "run", _make_subprocess_fake(calls))

        with pytest.raises(SystemExit) as exc_info:
            _cli.main(["list"], home=tmp_path)

        assert exc_info.value.code == 0
        git_cmds = [c["cmd"] for c in calls if c["cmd"][:1] == ["git"]]
        assert git_cmds[0] == ["git", "pull", "--ff-only"]


class TestCommitSubcommand:
    """commitサブコマンド: 外部編集分のコミット・push、差分なしなら早期return。"""

    def test_commit_when_dirty(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """未コミット差分ありの場合、pull→add→commit→pushの順で呼び出される。"""
        notes = _setup_flag_and_notes(tmp_path)
        calls: list[_GitCall] = []

        def fake_run(cmd: list[str], *_args: object, **kwargs: object) -> subprocess.CompletedProcess[Any]:
            calls.append({"cmd": list(cmd), "kwargs": dict(kwargs)})
            if cmd[:3] == ["git", "status", "--porcelain"]:
                stdout: Any = " M feedback/inbox/x.md\n" if kwargs.get("text") else b" M feedback/inbox/x.md\n"
                return subprocess.CompletedProcess(cmd, returncode=0, stdout=stdout, stderr=stdout)
            return subprocess.CompletedProcess(cmd, returncode=0, stdout=b"", stderr=b"")

        monkeypatch.setattr(_cli.subprocess, "run", fake_run)

        with pytest.raises(SystemExit) as exc_info:
            _cli.main(["commit"], home=tmp_path)

        assert exc_info.value.code == 0
        git_cmds = [c["cmd"] for c in calls]
        assert git_cmds[0] == ["git", "pull", "--ff-only"]
        assert git_cmds[1][:3] == ["git", "status", "--porcelain"]
        assert git_cmds[2] == ["git", "add", "feedback/inbox"]
        assert git_cmds[3] == ["git", "commit", "-m", "chore: edit feedback items externally"]
        assert git_cmds[4] == ["git", "push"]
        assert calls[0]["kwargs"].get("cwd") == notes
        captured = capsys.readouterr()
        assert "外部編集分をコミット" in captured.out

    def test_commit_when_clean(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """未コミット差分なしの場合、commit・pushを呼ばず「差分なし」を出力する。"""
        _setup_flag_and_notes(tmp_path)
        calls: list[_GitCall] = []

        def fake_run(cmd: list[str], *_args: object, **kwargs: object) -> subprocess.CompletedProcess[Any]:
            calls.append({"cmd": list(cmd), "kwargs": dict(kwargs)})
            if cmd[:3] == ["git", "status", "--porcelain"]:
                stdout: Any = "" if kwargs.get("text") else b""
                return subprocess.CompletedProcess(cmd, returncode=0, stdout=stdout, stderr=stdout)
            return subprocess.CompletedProcess(cmd, returncode=0, stdout=b"", stderr=b"")

        monkeypatch.setattr(_cli.subprocess, "run", fake_run)

        with pytest.raises(SystemExit) as exc_info:
            _cli.main(["commit"], home=tmp_path)

        assert exc_info.value.code == 0
        commit_cmds = [c["cmd"] for c in calls if "commit" in c["cmd"] or c["cmd"][:2] == ["git", "push"]]
        assert commit_cmds == []
        captured = capsys.readouterr()
        assert "差分なし" in captured.out


class TestEnableSubcommand:
    """enableサブコマンド: フラグファイル不在時に作成、存在時は冪等。"""

    def test_enable_creates_flag(self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
        """フラグファイルが無い状態でも実行でき、生成される。"""
        flag = tmp_path / ".config" / "agent-toolkit" / "feedback-inbox.enabled"
        assert not flag.exists()

        with pytest.raises(SystemExit) as exc_info:
            _cli.main(["enable"], home=tmp_path)

        assert exc_info.value.code == 0
        assert flag.exists()
        captured = capsys.readouterr()
        assert "有効化しました" in captured.out
        assert "chezmoi apply" in captured.out

    def test_enable_idempotent(self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
        """既にフラグファイルが存在する場合は無動作で完了する。"""
        flag = tmp_path / ".config" / "agent-toolkit" / "feedback-inbox.enabled"
        flag.parent.mkdir(parents=True, exist_ok=True)
        flag.touch()

        with pytest.raises(SystemExit) as exc_info:
            _cli.main(["enable"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "既に有効です" in captured.out


class TestDisableSubcommand:
    """disableサブコマンド: フラグファイル存在時に削除、不在時は冪等。"""

    def test_disable_removes_flag(self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
        """フラグファイルが存在する場合は削除される。"""
        flag = tmp_path / ".config" / "agent-toolkit" / "feedback-inbox.enabled"
        flag.parent.mkdir(parents=True, exist_ok=True)
        flag.touch()

        with pytest.raises(SystemExit) as exc_info:
            _cli.main(["disable"], home=tmp_path)

        assert exc_info.value.code == 0
        assert not flag.exists()
        captured = capsys.readouterr()
        assert "無効化しました" in captured.out
        assert "chezmoi apply" in captured.out

    def test_disable_idempotent(self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
        """フラグファイルが存在しない場合は無動作で完了する。"""
        with pytest.raises(SystemExit) as exc_info:
            _cli.main(["disable"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "既に無効です" in captured.out

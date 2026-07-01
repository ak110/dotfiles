"""pytools.dotfiles_fb._cli の拡張サブコマンド・オプションのテスト。

`add --source`・`list`のpull実行・`commit`・`enable`・`disable`・`status`の単体テストを集約する。
既存サブコマンドのテストは`_cli_test.py`に分離する。
共通ヘルパーは`_cli_test.py`から再利用する。
"""

import pathlib
import subprocess
import typing
from typing import Any

import pytest

from pytools.dotfiles_fb import _cli
from pytools.dotfiles_fb._cli_test import (
    _FIXED_DT,
    _GitCall,
    _make_subprocess_fake,
    _setup_flag_and_notes,
    _write_feedback_file,
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
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()

        def fake_run(cmd: list[str], *_args: object, **kwargs: object) -> subprocess.CompletedProcess[Any]:
            if cmd == ["git", "-C", str(myrepo), "remote", "get-url", "origin"]:
                stdout: Any = (
                    "https://github.com/example/myrepo.git\n"
                    if kwargs.get("text")
                    else b"https://github.com/example/myrepo.git\n"
                )
                return subprocess.CompletedProcess(cmd, returncode=0, stdout=stdout, stderr="" if kwargs.get("text") else b"")
            empty: Any = "" if kwargs.get("text") else b""
            return subprocess.CompletedProcess(cmd, returncode=0, stdout=empty, stderr=empty)

        monkeypatch.setattr(_cli.subprocess, "run", fake_run)

        with pytest.raises(SystemExit) as exc_info:
            _cli.main(
                ["add", "--source=session-review", str(myrepo), "メッセージ"],
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
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()

        def fake_run(cmd: list[str], *_args: object, **kwargs: object) -> subprocess.CompletedProcess[Any]:
            if cmd == ["git", "-C", str(myrepo), "remote", "get-url", "origin"]:
                stdout: Any = (
                    "https://github.com/example/myrepo.git\n"
                    if kwargs.get("text")
                    else b"https://github.com/example/myrepo.git\n"
                )
                return subprocess.CompletedProcess(cmd, returncode=0, stdout=stdout, stderr="" if kwargs.get("text") else b"")
            empty: Any = "" if kwargs.get("text") else b""
            return subprocess.CompletedProcess(cmd, returncode=0, stdout=empty, stderr=empty)

        monkeypatch.setattr(_cli.subprocess, "run", fake_run)

        with pytest.raises(SystemExit) as exc_info:
            _cli.main(["add", str(myrepo), "メッセージ"], home=tmp_path, now=_FIXED_DT)

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

    def test_disable_idempotent(self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
        """フラグファイルが存在しない場合は無動作で完了する。"""
        with pytest.raises(SystemExit) as exc_info:
            _cli.main(["disable"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "既に無効です" in captured.out


class TestStatusSubcommand:
    """statusサブコマンド: 有効状態をexit codeと出力先で通知する。"""

    def test_status_disabled_when_flag_missing(self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
        """フラグファイル不在時はexit 1で標準エラー出力に無効案内を出力する。"""
        with pytest.raises(SystemExit) as exc_info:
            _cli.main(["status"], home=tmp_path)

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "feedback-inbox機能が無効" in captured.err
        assert captured.out == ""

    def test_status_disabled_when_notes_missing(self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
        """フラグありかつprivate-notes不在時はexit 1で標準エラー出力にクローン案内を出力する。"""
        flag = tmp_path / ".config" / "agent-toolkit" / "feedback-inbox.enabled"
        flag.parent.mkdir(parents=True, exist_ok=True)
        flag.touch()

        with pytest.raises(SystemExit) as exc_info:
            _cli.main(["status"], home=tmp_path)

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "private-notesが見つかりません" in captured.err
        assert "クローン" in captured.err
        assert captured.out == ""

    def test_status_enabled_when_both_present(self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
        """フラグとprivate-notesが両方揃っている場合はexit 0で標準出力に有効案内を出力する。"""
        flag = tmp_path / ".config" / "agent-toolkit" / "feedback-inbox.enabled"
        flag.parent.mkdir(parents=True, exist_ok=True)
        flag.touch()
        (tmp_path / "private-notes").mkdir()

        with pytest.raises(SystemExit) as exc_info:
            _cli.main(["status"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "feedback-inboxは有効" in captured.out
        assert captured.err == ""


class TestFeedbackFilenameCompleter:
    """argcomplete補完関数の挙動を検証する。"""

    def test_returns_md_files_only(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """inbox配下の`.md`のみ返し、他拡張子は除外する。"""
        notes = _setup_flag_and_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md")
        (notes / "feedback" / "inbox" / "note.txt").write_text("テキスト", encoding="utf-8")
        monkeypatch.setattr(_cli.pathlib.Path, "home", classmethod(lambda cls: tmp_path))

        # pylint: disable-next=protected-access
        result = _cli._feedback_filename_completer("")  # noqa: SLF001
        assert result == ["fb-001.md"]

    def test_returns_empty_when_feedback_dir_missing(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """feedback配下が存在しない場合は空リストを返す。"""
        monkeypatch.setattr(_cli.pathlib.Path, "home", classmethod(lambda cls: tmp_path))

        # pylint: disable-next=protected-access
        result = _cli._feedback_filename_completer("")  # noqa: SLF001
        assert result == []

    def test_filters_by_prefix(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """prefix一致のファイルのみ返す。"""
        notes = _setup_flag_and_notes(tmp_path)
        _write_feedback_file(notes, "20260101-001.md")
        _write_feedback_file(notes, "20260201-001.md")
        monkeypatch.setattr(_cli.pathlib.Path, "home", classmethod(lambda cls: tmp_path))

        # pylint: disable-next=protected-access
        result = _cli._feedback_filename_completer("20260101")  # noqa: SLF001
        assert result == ["20260101-001.md"]


def _editor_fake_run(
    action: typing.Callable[[pathlib.Path], int],
    myrepo: pathlib.Path | None = None,
    remote_url: str = "https://github.com/example/myrepo.git",
) -> typing.Callable[..., subprocess.CompletedProcess[Any]]:
    """エディター呼び出し時にactionを実行し戻り値をreturncodeとするsubprocess.run差し替えを返す。

    fake-editor以外のコマンドは終了コード0で成功扱いとする。
    myrepo指定時は`git -C <myrepo> remote get-url origin`にremote_urlを返す。
    """

    def fake_run(cmd: list[str], *_args: object, **kwargs: object) -> subprocess.CompletedProcess[Any]:
        empty: Any = "" if kwargs.get("text") else b""
        if cmd[0] == "fake-editor":
            returncode = action(pathlib.Path(cmd[1]))
            return subprocess.CompletedProcess(cmd, returncode=returncode, stdout=empty, stderr=empty)
        if myrepo is not None and cmd == ["git", "-C", str(myrepo), "remote", "get-url", "origin"]:
            stdout: Any = f"{remote_url}\n" if kwargs.get("text") else f"{remote_url}\n".encode()
            return subprocess.CompletedProcess(cmd, returncode=0, stdout=stdout, stderr=empty)
        return subprocess.CompletedProcess(cmd, returncode=0, stdout=empty, stderr=empty)

    return fake_run


class TestAddViaEditor:
    """addサブコマンド: messages省略時に$EDITOR経由で本文を収集する。

    `_editor_fake_run`でエディター呼び出しを差し替え、subprocess.run全呼び出しを
    捕捉する。エラー経路のテストでは`_pull`等のgit呼び出しもfake_runへ吸収されるが、
    検証焦点は`_collect_message_via_editor`の早期None返却にあり、git経路到達有無は
    別経路（feedbackディレクトリへのファイル生成有無）で間接確認する。
    """

    def test_editor_path_generates_file_with_content(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """messages省略時にエディターが呼ばれ書き込み内容がfeedbackへ保存される。"""
        notes = _setup_flag_and_notes(tmp_path)
        monkeypatch.setenv("EDITOR", "fake-editor")
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()

        def write_body(tmp: pathlib.Path) -> int:
            tmp.write_text("エディター経由の本文\n", encoding="utf-8")
            return 0

        monkeypatch.setattr(_cli.subprocess, "run", _editor_fake_run(write_body, myrepo=myrepo))

        with pytest.raises(SystemExit) as exc_info:
            _cli.main(["add", str(myrepo)], home=tmp_path, now=_FIXED_DT)

        assert exc_info.value.code == 0
        files = list((notes / "feedback" / "inbox").iterdir())
        assert len(files) == 1
        content = files[0].read_text(encoding="utf-8")
        assert "エディター経由の本文" in content

        captured = capsys.readouterr()
        assert "編集する場合:\n" in captured.out
        assert f"  dotfiles-fb edit {files[0].name}\n" in captured.out

    def test_editor_empty_save_aborts(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """エディター保存内容がstrip後に空の場合はexit 1で投入中止する。"""
        notes = _setup_flag_and_notes(tmp_path)
        monkeypatch.setenv("EDITOR", "fake-editor")
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()

        def write_blanks(tmp: pathlib.Path) -> int:
            tmp.write_text("   \n\n", encoding="utf-8")
            return 0

        monkeypatch.setattr(_cli.subprocess, "run", _editor_fake_run(write_blanks, myrepo=myrepo))

        with pytest.raises(SystemExit) as exc_info:
            _cli.main(["add", str(myrepo)], home=tmp_path, now=_FIXED_DT)

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "本文が空" in captured.err
        assert not list((notes / "feedback" / "inbox").iterdir())

    def test_editor_missing_env_exits(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """$EDITOR未設定時はexit 1で案内が出力される。"""
        _setup_flag_and_notes(tmp_path)
        monkeypatch.delenv("EDITOR", raising=False)
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()

        def fake_run(cmd: list[str], *_args: object, **kwargs: object) -> subprocess.CompletedProcess[Any]:
            if cmd == ["git", "-C", str(myrepo), "remote", "get-url", "origin"]:
                stdout: Any = (
                    "https://github.com/example/myrepo.git\n"
                    if kwargs.get("text")
                    else b"https://github.com/example/myrepo.git\n"
                )
                return subprocess.CompletedProcess(cmd, returncode=0, stdout=stdout, stderr="" if kwargs.get("text") else b"")
            empty: Any = "" if kwargs.get("text") else b""
            return subprocess.CompletedProcess(cmd, returncode=0, stdout=empty, stderr=empty)

        monkeypatch.setattr(_cli.subprocess, "run", fake_run)

        with pytest.raises(SystemExit) as exc_info:
            _cli.main(["add", str(myrepo)], home=tmp_path, now=_FIXED_DT)

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "EDITOR" in captured.err

    def test_editor_nonzero_exit_aborts(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """エディターが非ゼロ終了したらexit 1で案内する。"""
        notes = _setup_flag_and_notes(tmp_path)
        monkeypatch.setenv("EDITOR", "fake-editor")
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()

        monkeypatch.setattr(_cli.subprocess, "run", _editor_fake_run(lambda _tmp: 2, myrepo=myrepo))

        with pytest.raises(SystemExit) as exc_info:
            _cli.main(["add", str(myrepo)], home=tmp_path, now=_FIXED_DT)

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "終了コード2" in captured.err
        assert not list((notes / "feedback" / "inbox").iterdir())

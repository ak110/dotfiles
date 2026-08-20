"""atk (agent-toolkit `atk mq`) の拡張サブコマンド・オプションのテスト。

`add --source`・`list`/`show`のremote同期・`commit`・`list`の状態に基づく抽出・
エディター経由の`add`・ファイルパス誤投入拒否・`mq add`の`--target-repo`の単体テストを集約する。
既存サブコマンドのテストは`atk_test.py`に分離する。
共通ヘルパーは`atk_test.py`・`_atk_git_fake_test_helpers.py`から再利用する。
"""

import pathlib
import subprocess
import sys
import typing
from typing import Any

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import atk  # noqa: E402  # pylint: disable=wrong-import-position
from _atk_git_fake_test_helpers import _FIXED_HEAD_COMMIT  # noqa: E402  # pylint: disable=wrong-import-position

# pylint: disable-next=wrong-import-position,import-error
from _atk_git_fake_test_helpers import fake_git_worktree_remote_response as _fake_git_worktree_remote_response  # noqa: E402

# pylint: disable-next=wrong-import-position,import-error
from _atk_git_fake_test_helpers import make_git_remote_fake as _make_git_remote_fake  # noqa: E402
from atk_test import (  # noqa: E402  # pylint: disable=wrong-import-position
    _FIXED_DT,
    _FIXED_TIMESTAMP,
    _GitCall,
    _make_subprocess_fake,
    _setup_notes,
    _write_feedback_file,
    _write_tbd_file,
)


class TestAddSourceOption:
    """addサブコマンド: --source指定時にfrontmatterへsource行を記録する。"""

    def test_source_recorded_when_given(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """--source=session-review指定時、frontmatterにsource: session-reviewが含まれる。"""
        notes = _setup_notes(tmp_path)
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()

        monkeypatch.setattr(subprocess, "run", _make_git_remote_fake(myrepo))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(
                ["mq", "add", "--source=session-review", str(myrepo), "メッセージ"],
                home=tmp_path,
                now=_FIXED_DT,
            )

        assert exc_info.value.code == 0
        content = next((notes / "inbox").iterdir()).read_text(encoding="utf-8")
        assert "source: session-review" in content

    def test_source_absent_when_not_given(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """--source未指定時、frontmatterにsource行が含まれない。"""
        notes = _setup_notes(tmp_path)
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()

        monkeypatch.setattr(subprocess, "run", _make_git_remote_fake(myrepo))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "add", str(myrepo), "メッセージ"], home=tmp_path, now=_FIXED_DT)

        assert exc_info.value.code == 0
        content = next((notes / "inbox").iterdir()).read_text(encoding="utf-8")
        assert "source:" not in content


class TestListPullsBeforeRead:
    """listサブコマンド: 出力前に明示したupstreamへ同期する。"""

    def test_list_pulls_before_reading(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """list実行時にfetchと明示upstreamへのfast-forward統合を行うこと。"""
        _setup_notes(tmp_path)
        calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "list"], home=tmp_path)

        assert exc_info.value.code == 0
        git_cmds = [c["cmd"] for c in calls if c["cmd"][:1] == ["git"]]
        assert git_cmds[:2] == [["git", "fetch"], ["git", "merge", "--ff-only", "@{u}"]]


class TestShowAllPullsBeforeRead:
    """showサブコマンド: --all指定時も出力前に明示したupstreamへ同期する。"""

    def test_show_all_pulls_before_reading(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """show --all実行時にfetchと明示upstreamへのfast-forward統合を行うこと。"""
        _setup_notes(tmp_path)
        calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "show", "--all"], home=tmp_path)

        assert exc_info.value.code == 0
        git_cmds = [c["cmd"] for c in calls if c["cmd"][:1] == ["git"]]
        assert git_cmds[:2] == [["git", "fetch"], ["git", "merge", "--ff-only", "@{u}"]]


class TestCommitSubcommand:
    """commitサブコマンド: 外部編集分のコミット・push、差分なしなら早期return。"""

    def test_commit_when_dirty(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """未コミット差分ありの場合、remote同期→add→commit→pushの順で呼び出される。"""
        notes = _setup_notes(tmp_path)
        calls: list[_GitCall] = []

        def fake_run(cmd: list[str], *_args: object, **kwargs: object) -> subprocess.CompletedProcess[Any]:
            calls.append({"cmd": list(cmd), "kwargs": dict(kwargs)})
            if cmd[:3] == ["git", "status", "--porcelain"]:
                stdout: Any = " M inbox/x.md\n" if kwargs.get("text") else b" M inbox/x.md\n"
                return subprocess.CompletedProcess(cmd, returncode=0, stdout=stdout, stderr=stdout)
            return subprocess.CompletedProcess(cmd, returncode=0, stdout=b"", stderr=b"")

        monkeypatch.setattr(subprocess, "run", fake_run)

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "commit"], home=tmp_path)

        assert exc_info.value.code == 0
        git_cmds = [c["cmd"] for c in calls]
        assert git_cmds[:2] == [["git", "fetch"], ["git", "merge", "--ff-only", "@{u}"]]
        assert git_cmds[2][:3] == ["git", "status", "--porcelain"]
        assert git_cmds[3] == ["git", "add", "inbox", "processing"]
        assert git_cmds[4] == ["git", "commit", "-m", "chore: edit queue items externally"]
        assert git_cmds[5] == ["git", "push"]
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
        _setup_notes(tmp_path)
        calls: list[_GitCall] = []

        def fake_run(cmd: list[str], *_args: object, **kwargs: object) -> subprocess.CompletedProcess[Any]:
            calls.append({"cmd": list(cmd), "kwargs": dict(kwargs)})
            if cmd[:3] == ["git", "status", "--porcelain"]:
                stdout: Any = "" if kwargs.get("text") else b""
                return subprocess.CompletedProcess(cmd, returncode=0, stdout=stdout, stderr=stdout)
            return subprocess.CompletedProcess(cmd, returncode=0, stdout=b"", stderr=b"")

        monkeypatch.setattr(subprocess, "run", fake_run)

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "commit"], home=tmp_path)

        assert exc_info.value.code == 0
        commit_cmds = [c["cmd"] for c in calls if "commit" in c["cmd"] or c["cmd"][:2] == ["git", "push"]]
        assert commit_cmds == []
        captured = capsys.readouterr()
        assert "差分なし" in captured.out


def _write_processing_file(
    notes: pathlib.Path,
    filename: str,
    target_repo: str = "github.com/example/foo",
    body: str = "処理中本文",
) -> pathlib.Path:
    """processing配下に1ファイルを書き込み、絶対パスを返す。"""
    processing_dir = notes / "processing"
    processing_dir.mkdir(parents=True, exist_ok=True)
    path = processing_dir / filename
    path.write_text(
        f"---\ntype: feedback\ntarget_repo: {target_repo}\n---\n\n{body}\n",
        encoding="utf-8",
    )
    return path


def _write_adopted_file(
    notes: pathlib.Path,
    filename: str,
    target_repo: str = "github.com/example/foo",
    body: str = "採用済み本文",
) -> pathlib.Path:
    """adopted配下にファイルを書き込み、絶対パスを返す。"""
    adopted_dir = notes / "adopted"
    adopted_dir.mkdir(parents=True, exist_ok=True)
    path = adopted_dir / filename
    path.write_text(
        f"---\ntype: feedback\ntarget_repo: {target_repo}\n---\n\n{body}\n\n## 処理結果\n\n- 採否: adopted\n",
        encoding="utf-8",
    )
    return path


class TestListFeedbackStatusDefaultAll:
    """`list`サブコマンド既定: フィードバックは`inbox`・`processing`両方を表示する。"""

    def test_default_shows_inbox_and_processing(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """`--status`省略時、フィードバック側は`inbox`配下と`processing`配下の両方を出力する。"""
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "fb-inbox.md", body="in-body")
        _write_processing_file(notes, "fb-proc.md", body="proc-body")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "list", "--type=feedback"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "fb-inbox.md" in captured.out
        assert "fb-proc.md" in captured.out


class TestListFeedbackStatusProcessing:
    """listサブコマンド `--status=processing`: フィードバックは`processing`配下のみを表示する。"""

    def test_processing_shows_processing_only(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """`--status=processing`指定時、フィードバック側は`processing`配下のみ出力する。"""
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "fb-inbox.md", body="in-body")
        _write_processing_file(notes, "fb-proc.md", body="proc-body")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "list", "--type=feedback", "--status=processing"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "fb-inbox.md" not in captured.out
        assert "fb-proc.md" in captured.out


class TestListFeedbackStatusAdopted:
    """listサブコマンド `--status=adopted`: フィードバックは`adopted`配下のみを表示する。"""

    def test_adopted_shows_adopted_only(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """`--status=adopted`指定時、フィードバック側は`adopted`配下のみ出力する。"""
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "fb-inbox.md", body="in-body")
        _write_processing_file(notes, "fb-proc.md", body="proc-body")
        _write_adopted_file(notes, "fb-adopted.md", body="adopted-body")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "list", "--type=feedback", "--status=adopted"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "fb-inbox.md" not in captured.out
        assert "fb-proc.md" not in captured.out
        assert "fb-adopted.md" in captured.out


class TestListFeedbackStatusAll:
    """listサブコマンド `--status=all`: フィードバックは`inbox`・`processing`双方を表示する。"""

    def test_all_shows_both(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """`--status=all`指定時、フィードバック側は`inbox`・`processing`両方を出力する。"""
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "fb-inbox.md", body="in-body")
        _write_processing_file(notes, "fb-proc.md", body="proc-body")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "list", "--type=feedback", "--status=all"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "fb-inbox.md" in captured.out
        assert "fb-proc.md" in captured.out


class TestListFeedbackStatusActive:
    """listサブコマンド `--status=active`: フィードバックは`inbox`・`processing`のみを表示し`adopted`・`rejected`を除外する。"""

    def test_active_excludes_adopted_and_rejected(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """`--status=active`指定時、フィードバック側は`adopted`・`rejected`配下を除外し`inbox`・`processing`のみ出力する。"""
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "fb-inbox.md", body="in-body")
        _write_processing_file(notes, "fb-proc.md", body="proc-body")
        _write_adopted_file(notes, "fb-adopted.md", body="adopted-body")
        rejected_dir = notes / "rejected"
        rejected_dir.mkdir(parents=True, exist_ok=True)
        (rejected_dir / "fb-rejected.md").write_text(
            "---\ntype: feedback\ntarget_repo: github.com/example/foo\n---\n\nrejected-body\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "list", "--type=feedback", "--status=active"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "fb-inbox.md: github.com/example/foo [inbox/normal/ready] in-body" in captured.out
        assert "fb-proc.md: github.com/example/foo [processing/normal/ready] proc-body" in captured.out
        assert "fb-adopted.md" not in captured.out
        assert "fb-rejected.md" not in captured.out

    def test_default_status_matches_explicit_active(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """`--status`省略時、フィードバック側は`adopted`配下を除外し、`tbd`側は未回答を除外する（`--status=active`と同じ結果）。"""
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "fb-inbox.md", body="inbox本文")
        _write_adopted_file(notes, "fb-adopted.md", body="adopted本文")
        _write_tbd_file(notes, f"{_FIXED_TIMESTAMP}-001.md", question="q1", answer="")
        _write_tbd_file(notes, f"{_FIXED_TIMESTAMP}-002.md", question="q2", answer="回答あり\n")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "list"], home=tmp_path)
        assert exc_info.value.code == 0
        default_out = capsys.readouterr().out

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "list", "--status=active"], home=tmp_path)
        assert exc_info.value.code == 0
        active_out = capsys.readouterr().out

        assert default_out == active_out
        assert "fb-inbox.md: github.com/example/foo [inbox/normal/ready] inbox本文" in default_out
        assert "fb-adopted.md" not in default_out
        assert f"{_FIXED_TIMESTAMP}-001.md" in default_out
        assert f"{_FIXED_TIMESTAMP}-002.md" in default_out


class TestListFeedbackStatusRejected:
    """listサブコマンド `--status=rejected`: フィードバックは`rejected`配下のみを表示する。"""

    def test_rejected_shows_rejected_only(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """`--status=rejected`指定時、フィードバック側は`rejected`配下のみ出力する。"""
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "fb-inbox.md", body="in-body")
        rejected_dir = notes / "rejected"
        rejected_dir.mkdir(parents=True, exist_ok=True)
        (rejected_dir / "fb-rejected.md").write_text(
            "---\ntype: feedback\ntarget_repo: github.com/example/foo\n---\n\nrejected-body\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "list", "--type=feedback", "--status=rejected"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "fb-inbox.md" not in captured.out
        assert "fb-rejected.md: github.com/example/foo [rejected/normal/complete] rejected-body" in captured.out

    def test_rejected_does_not_affect_tbd(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """`--status=rejected`指定時、tbd側は状態フォルダを持たないため全件出力される。"""
        notes = _setup_notes(tmp_path)
        (notes / "inbox").mkdir(parents=True, exist_ok=True)
        _write_tbd_file(notes, f"{_FIXED_TIMESTAMP}-001.md", question="q1", answer="")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "list", "--status=rejected"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert f"{_FIXED_TIMESTAMP}-001.md" in captured.err


def _editor_fake_run(
    action: typing.Callable[[pathlib.Path], int],
    myrepo: pathlib.Path | None = None,
    remote_url: str = "https://github.com/example/myrepo.git",
) -> typing.Callable[..., subprocess.CompletedProcess[Any]]:
    """エディター呼び出し時にactionを実行し戻り値をreturncodeとするsubprocess.run差し替えを返す。

    fake-editor以外のコマンドは終了コード0で成功扱いとする。
    myrepo指定時は`git rev-parse --show-toplevel`にmyrepoを、
    `git -C <myrepo>`のremote URL取得とHEAD取得へ固定値を返す（対象リポジトリはcwdから解決される）。
    """

    def fake_run(cmd: list[str], *_args: object, **kwargs: object) -> subprocess.CompletedProcess[Any]:
        empty: Any = "" if kwargs.get("text") else b""
        if cmd[0] == "fake-editor":
            returncode = action(pathlib.Path(cmd[1]))
            return subprocess.CompletedProcess(cmd, returncode=returncode, stdout=empty, stderr=empty)
        if myrepo is not None and cmd == ["git", "rev-parse", "--show-toplevel"]:
            stdout: Any = f"{myrepo}\n" if kwargs.get("text") else f"{myrepo}\n".encode()
            return subprocess.CompletedProcess(cmd, returncode=0, stdout=stdout, stderr=empty)
        if myrepo is not None and cmd == ["git", "-C", str(myrepo), "remote", "get-url", "origin"]:
            remote_stdout: Any = f"{remote_url}\n" if kwargs.get("text") else f"{remote_url}\n".encode()
            return subprocess.CompletedProcess(cmd, returncode=0, stdout=remote_stdout, stderr=empty)
        if myrepo is not None and cmd == ["git", "-C", str(myrepo), "rev-parse", "--is-inside-work-tree"]:
            stdout = "true\n" if kwargs.get("text") else b"true\n"
            return subprocess.CompletedProcess(cmd, returncode=0, stdout=stdout, stderr=empty)
        if myrepo is not None and cmd == ["git", "-C", str(myrepo), "rev-parse", "--verify", "HEAD^{commit}"]:
            stdout = f"{_FIXED_HEAD_COMMIT}\n" if kwargs.get("text") else f"{_FIXED_HEAD_COMMIT}\n".encode()
            return subprocess.CompletedProcess(cmd, returncode=0, stdout=stdout, stderr=empty)
        return subprocess.CompletedProcess(cmd, returncode=0, stdout=empty, stderr=empty)

    return fake_run


class TestAddViaEditor:
    """addサブコマンド: messages省略時に$EDITOR経由で本文を収集する。

    `_editor_fake_run`でエディター呼び出しを差し替え、subprocess.run全呼び出しを
    捕捉する。エラー経路のテストでは`_pull`等のgit呼び出しもfake_runへ吸収されるが、
    検証焦点は`_collect_message_via_editor`の早期None返却にあり、git経路到達有無は
    別経路（フィードバックディレクトリへのファイル生成有無）で間接確認する。
    """

    def test_editor_path_generates_file_with_content(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """messages省略時にエディターが呼ばれ書き込み内容がフィードバックへ保存される。"""
        notes = _setup_notes(tmp_path)
        monkeypatch.setenv("EDITOR", "fake-editor")
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()

        def write_body(tmp: pathlib.Path) -> int:
            tmp.write_text("エディター経由の本文\n", encoding="utf-8")
            return 0

        monkeypatch.setattr(subprocess, "run", _editor_fake_run(write_body, myrepo=myrepo))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "add"], home=tmp_path, now=_FIXED_DT)

        assert exc_info.value.code == 0
        files = list((notes / "inbox").iterdir())
        assert len(files) == 1
        content = files[0].read_text(encoding="utf-8")
        assert "エディター経由の本文" in content

        captured = capsys.readouterr()
        assert "編集する場合:\n" in captured.out
        assert f"  atk mq edit {files[0].name}\n" in captured.out

    def test_editor_empty_save_aborts(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """エディター保存内容がstrip後に空の場合はexit 1で投入中止する。"""
        notes = _setup_notes(tmp_path)
        monkeypatch.setenv("EDITOR", "fake-editor")
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()

        def write_blanks(tmp: pathlib.Path) -> int:
            tmp.write_text("   \n\n", encoding="utf-8")
            return 0

        monkeypatch.setattr(subprocess, "run", _editor_fake_run(write_blanks, myrepo=myrepo))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "add"], home=tmp_path, now=_FIXED_DT)

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "本文が空" in captured.err
        assert not list((notes / "inbox").iterdir())

    def test_editor_missing_env_exits(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """$EDITOR未設定時はexit 1で案内が出力される。"""
        _setup_notes(tmp_path)
        monkeypatch.delenv("EDITOR", raising=False)
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()

        def fake_run(cmd: list[str], *_args: object, **kwargs: object) -> subprocess.CompletedProcess[Any]:
            resp = _fake_git_worktree_remote_response(cmd, myrepo, kwargs)
            if resp is not None:
                return resp
            empty: Any = "" if kwargs.get("text") else b""
            return subprocess.CompletedProcess(cmd, returncode=0, stdout=empty, stderr=empty)

        monkeypatch.setattr(subprocess, "run", fake_run)

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "add"], home=tmp_path, now=_FIXED_DT)

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
        notes = _setup_notes(tmp_path)
        monkeypatch.setenv("EDITOR", "fake-editor")
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()

        monkeypatch.setattr(subprocess, "run", _editor_fake_run(lambda _tmp: 2, myrepo=myrepo))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "add"], home=tmp_path, now=_FIXED_DT)

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "終了コード2" in captured.err
        assert not list((notes / "inbox").iterdir())


class TestAddFilePathArgumentRejected:
    """`mq add`の位置引数がファイルパスに解釈される場合の誤操作拒否を検証する。"""

    def test_md_file_path_argument_rejected(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """既存の`.md`ファイルパスを単独の位置引数として渡すと拒否される。"""
        notes = _setup_notes(tmp_path)
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()
        body_file = tmp_path / "body.md"
        body_file.write_text("投入したい本文", encoding="utf-8")

        def fake_run(cmd: list[str], *_args: object, **kwargs: object) -> subprocess.CompletedProcess[Any]:
            resp = _fake_git_worktree_remote_response(cmd, myrepo, kwargs)
            if resp is not None:
                return resp
            empty: Any = "" if kwargs.get("text") else b""
            return subprocess.CompletedProcess(cmd, returncode=0, stdout=empty, stderr=empty)

        monkeypatch.setattr(subprocess, "run", fake_run)

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "add", str(myrepo), str(body_file)], home=tmp_path, now=_FIXED_DT)

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "ファイルパス" in captured.err
        assert not list((notes / "inbox").glob("*.md"))

    def test_nonexistent_md_path_string_not_rejected(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """`.md`で終わるが実在しないパス文字列は本文として通常投入される。"""
        notes = _setup_notes(tmp_path)
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()
        monkeypatch.chdir(tmp_path)

        def fake_run(cmd: list[str], *_args: object, **kwargs: object) -> subprocess.CompletedProcess[Any]:
            resp = _fake_git_worktree_remote_response(cmd, myrepo, kwargs)
            if resp is not None:
                return resp
            empty: Any = "" if kwargs.get("text") else b""
            return subprocess.CompletedProcess(cmd, returncode=0, stdout=empty, stderr=empty)

        monkeypatch.setattr(subprocess, "run", fake_run)

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "add", str(myrepo), "docs/architecture.md"], home=tmp_path, now=_FIXED_DT)

        assert exc_info.value.code == 0
        content = next((notes / "inbox").iterdir()).read_text(encoding="utf-8")
        assert "docs/architecture.md" in content

    def test_extensionless_temp_file_argument_rejected(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """`mktemp`生成の拡張子なし一時ファイルパスを単独の位置引数として渡すと拒否される。"""
        notes = _setup_notes(tmp_path)
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()
        body_file = tmp_path / "tmp.abc123"
        body_file.write_text("投入したい本文", encoding="utf-8")

        def fake_run(cmd: list[str], *_args: object, **kwargs: object) -> subprocess.CompletedProcess[Any]:
            resp = _fake_git_worktree_remote_response(cmd, myrepo, kwargs)
            if resp is not None:
                return resp
            empty: Any = "" if kwargs.get("text") else b""
            return subprocess.CompletedProcess(cmd, returncode=0, stdout=empty, stderr=empty)

        monkeypatch.setattr(subprocess, "run", fake_run)

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "add", str(myrepo), str(body_file)], home=tmp_path, now=_FIXED_DT)

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "ファイルパス" in captured.err
        assert not list((notes / "inbox").glob("*.md"))


class TestAddTargetRepoOption:
    """`mq add --target-repo`の投入先指定とfrontmatter置換を検証する。"""

    def test_target_repo_option_used_as_fallback(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """`--target-repo`指定時、frontmatter未指定の本文はCLI値がtarget_repoとして記録される。"""
        notes = _setup_notes(tmp_path)
        cwd_repo = tmp_path / "cwdrepo"
        cwd_repo.mkdir()

        def fake_run(cmd: list[str], *_args: object, **kwargs: object) -> subprocess.CompletedProcess[Any]:
            resp = _fake_git_worktree_remote_response(cmd, cwd_repo, kwargs)
            if resp is not None:
                return resp
            empty: Any = "" if kwargs.get("text") else b""
            return subprocess.CompletedProcess(cmd, returncode=0, stdout=empty, stderr=empty)

        monkeypatch.setattr(subprocess, "run", fake_run)

        with pytest.raises(SystemExit) as exc_info:
            atk.main(
                ["mq", "add", "--target-repo", "github.com/example/otherrepo", "本文"],
                home=tmp_path,
                now=_FIXED_DT,
            )

        assert exc_info.value.code == 0
        content = next((notes / "inbox").iterdir()).read_text(encoding="utf-8")
        assert "target_repo: github.com/example/otherrepo" in content

    def test_explicit_target_repo_replaces_frontmatter_target_repo(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """frontmatterでtarget_repoが明示されても`--target-repo`の値へ置き換える。"""
        notes = _setup_notes(tmp_path)
        cwd_repo = tmp_path / "cwdrepo"
        cwd_repo.mkdir()

        def fake_run(cmd: list[str], *_args: object, **kwargs: object) -> subprocess.CompletedProcess[Any]:
            resp = _fake_git_worktree_remote_response(cmd, cwd_repo, kwargs)
            if resp is not None:
                return resp
            empty: Any = "" if kwargs.get("text") else b""
            return subprocess.CompletedProcess(cmd, returncode=0, stdout=empty, stderr=empty)

        monkeypatch.setattr(subprocess, "run", fake_run)

        frontmatter_body = "---\ntarget_repo: github.com/example/fmrepo\n---\n\n本文"
        with pytest.raises(SystemExit) as exc_info:
            atk.main(
                ["mq", "add", "--target-repo", "github.com/example/otherrepo", frontmatter_body],
                home=tmp_path,
                now=_FIXED_DT,
            )

        assert exc_info.value.code == 0
        content = next((notes / "inbox").iterdir()).read_text(encoding="utf-8")
        assert "target_repo: github.com/example/otherrepo" in content
        assert "target_repo: github.com/example/fmrepo" not in content


class TestTbdAddTargetRepoOption:
    """`mq add --type=tbd --target-repo`のfallback指定・レガシー位置引数との優先順位を検証する。"""

    def test_target_repo_option_used_as_fallback(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """`--target-repo`指定時、位置引数を省略した呼び出しではCLI値がtarget_repoとして記録される。"""
        notes = _setup_notes(tmp_path)
        cwd_repo = tmp_path / "cwdrepo"
        cwd_repo.mkdir()
        monkeypatch.setattr(subprocess, "run", _make_git_remote_fake(cwd_repo))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(
                ["mq", "add", "--type=tbd", "--target-repo", "github.com/example/otherrepo", "未確認の挙動？"],
                home=tmp_path,
                now=_FIXED_DT,
            )
        assert exc_info.value.code == 0

        files = sorted((notes / "inbox").iterdir())
        content = files[0].read_text(encoding="utf-8")
        assert "target_repo: github.com/example/otherrepo" in content

    def test_legacy_repo_path_takes_priority_over_target_repo_option(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """旧REPO_PATH位置引数形式が併用された場合、`--target-repo`より優先される。"""
        notes = _setup_notes(tmp_path)
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()
        monkeypatch.setattr(subprocess, "run", _make_git_remote_fake(myrepo))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(
                ["mq", "add", "--type=tbd", str(myrepo), "--target-repo", "github.com/example/otherrepo", "未確認の挙動？"],
                home=tmp_path,
                now=_FIXED_DT,
            )
        assert exc_info.value.code == 0

        files = sorted((notes / "inbox").iterdir())
        content = files[0].read_text(encoding="utf-8")
        assert "target_repo: github.com/example/myrepo" in content

"""atk (agent-toolkit `atk fb`) の拡張サブコマンド・オプションのテスト。

`add --source`・`list`/`show`のpull実行・`commit`・`enable`・`disable`・`status`の単体テストを集約する。
既存サブコマンドのテストは`atk_test.py`に分離する。
共通ヘルパーは`atk_test.py`から再利用する。
"""

import pathlib
import subprocess
import sys
import typing
from typing import Any

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import atk  # noqa: E402  # pylint: disable=wrong-import-position
from atk_test import (  # noqa: E402  # pylint: disable=wrong-import-position
    _FIXED_DT,
    _FIXED_TIMESTAMP,
    _GitCall,
    _make_subprocess_fake,
    _setup_flag_and_notes,
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

        monkeypatch.setattr(subprocess, "run", fake_run)

        with pytest.raises(SystemExit) as exc_info:
            atk.main(
                ["fb", "add", "--source=session-review", str(myrepo), "メッセージ"],
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

        monkeypatch.setattr(subprocess, "run", fake_run)

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["fb", "add", str(myrepo), "メッセージ"], home=tmp_path, now=_FIXED_DT)

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
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["fb", "list"], home=tmp_path)

        assert exc_info.value.code == 0
        git_cmds = [c["cmd"] for c in calls if c["cmd"][:1] == ["git"]]
        assert git_cmds[0] == ["git", "pull", "--ff-only"]


class TestShowAllPullsBeforeRead:
    """showサブコマンド: --all指定時も出力前にgit pull --ff-onlyを実行する。"""

    def test_show_all_pulls_before_reading(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """show --all実行時に最初のgit呼び出しがpullであること。"""
        _setup_flag_and_notes(tmp_path)
        calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["fb", "show", "--all"], home=tmp_path)

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

        monkeypatch.setattr(subprocess, "run", fake_run)

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["fb", "commit"], home=tmp_path)

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

        monkeypatch.setattr(subprocess, "run", fake_run)

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["fb", "commit"], home=tmp_path)

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
    """feedback/processing配下に1ファイルを書き込み、絶対パスを返す。"""
    processing_dir = notes / "feedback" / "processing"
    processing_dir.mkdir(parents=True, exist_ok=True)
    path = processing_dir / filename
    path.write_text(
        f"---\ntarget_repo: {target_repo}\n---\n\n{body}\n",
        encoding="utf-8",
    )
    return path


def _write_adopted_file(
    notes: pathlib.Path,
    filename: str,
    category: str,
    target_repo: str = "github.com/example/foo",
    body: str = "採用済み本文",
) -> pathlib.Path:
    """feedback/adopted配下にカテゴリ付きファイルを書き込み、絶対パスを返す。"""
    adopted_dir = notes / "feedback" / "adopted"
    adopted_dir.mkdir(parents=True, exist_ok=True)
    path = adopted_dir / filename
    path.write_text(
        f"---\ntarget_repo: {target_repo}\n---\n\n{body}\n\n## 処理結果\n\n- 採否: adopted\n- カテゴリ: {category}\n",
        encoding="utf-8",
    )
    return path


class TestListFeedbackStatusDefaultAll:
    """listサブコマンド既定: feedbackはinbox・processing両方を表示する。"""

    def test_default_shows_inbox_and_processing(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """`--status`省略時、feedback側はinbox配下とprocessing配下の両方を出力する。"""
        notes = _setup_flag_and_notes(tmp_path)
        _write_feedback_file(notes, "fb-inbox.md", body="in-body")
        _write_processing_file(notes, "fb-proc.md", body="proc-body")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["fb", "list", "--type=feedback"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "fb-inbox.md" in captured.out
        assert "fb-proc.md" in captured.out


class TestListFeedbackStatusProcessing:
    """listサブコマンド `--status=processing`: feedbackはprocessing配下のみを表示する。"""

    def test_processing_shows_processing_only(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """`--status=processing`指定時、feedback側はprocessing配下のみ出力する。"""
        notes = _setup_flag_and_notes(tmp_path)
        _write_feedback_file(notes, "fb-inbox.md", body="in-body")
        _write_processing_file(notes, "fb-proc.md", body="proc-body")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["fb", "list", "--type=feedback", "--status=processing"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "fb-inbox.md" not in captured.out
        assert "fb-proc.md" in captured.out


class TestListFeedbackStatusAdopted:
    """listサブコマンド `--status=adopted`: feedbackはadopted配下のみを表示する。"""

    def test_adopted_shows_adopted_only(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """`--status=adopted`指定時、feedback側はadopted配下のみ出力する。"""
        notes = _setup_flag_and_notes(tmp_path)
        _write_feedback_file(notes, "fb-inbox.md", body="in-body")
        _write_processing_file(notes, "fb-proc.md", body="proc-body")
        _write_adopted_file(notes, "fb-adopted.md", category="scope-escalation", body="adopted-body")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["fb", "list", "--type=feedback", "--status=adopted"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "fb-inbox.md" not in captured.out
        assert "fb-proc.md" not in captured.out
        assert "fb-adopted.md" in captured.out


class TestListFeedbackCategory:
    """listサブコマンド `--category`: feedbackを指定カテゴリへ限定する。"""

    def test_category_filter_limits_feedback_entries(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """`--category`指定時、同カテゴリが付与されたfeedbackのみ出力する。"""
        notes = _setup_flag_and_notes(tmp_path)
        _write_adopted_file(notes, "fb-scope.md", category="scope-escalation", body="scope-body")
        _write_adopted_file(notes, "fb-other.md", category="other", body="other-body")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(
                ["fb", "list", "--type=feedback", "--status=adopted", "--category", "scope-escalation"],
                home=tmp_path,
            )

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "fb-scope.md" in captured.out
        assert "fb-other.md" not in captured.out


class TestListFeedbackStatusAll:
    """listサブコマンド `--status=all`: feedbackはinbox・processing双方を表示する。"""

    def test_all_shows_both(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """`--status=all`指定時、feedback側はinbox・processing両方を出力する。"""
        notes = _setup_flag_and_notes(tmp_path)
        _write_feedback_file(notes, "fb-inbox.md", body="in-body")
        _write_processing_file(notes, "fb-proc.md", body="proc-body")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["fb", "list", "--type=feedback", "--status=all"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "fb-inbox.md" in captured.out
        assert "fb-proc.md" in captured.out


class TestListFeedbackStatusActive:
    """listサブコマンド `--status=active`: feedbackはinbox・processingのみを表示しadopted・rejectedを除外する。"""

    def test_active_excludes_adopted_and_rejected(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """`--status=active`指定時、feedback側はadopted・rejected配下を除外しinbox・processingのみ出力する。"""
        notes = _setup_flag_and_notes(tmp_path)
        _write_feedback_file(notes, "fb-inbox.md", body="in-body")
        _write_processing_file(notes, "fb-proc.md", body="proc-body")
        _write_adopted_file(notes, "fb-adopted.md", category="scope-escalation", body="adopted-body")
        rejected_dir = notes / "feedback" / "rejected"
        rejected_dir.mkdir(parents=True, exist_ok=True)
        (rejected_dir / "fb-rejected.md").write_text(
            "---\ntarget_repo: github.com/example/foo\n---\n\nrejected-body\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["fb", "list", "--type=feedback", "--status=active"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "fb-inbox.md: github.com/example/foo [inbox] in-body" in captured.out
        assert "fb-proc.md: github.com/example/foo [processing] proc-body" in captured.out
        assert "fb-adopted.md" not in captured.out
        assert "fb-rejected.md" not in captured.out

    def test_default_status_matches_explicit_active(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """`--status`省略時、feedback側はadopted配下を除外し、tbd側は未回答を除外する（`--status=active`と同じ結果）。"""
        notes = _setup_flag_and_notes(tmp_path)
        _write_feedback_file(notes, "fb-inbox.md", body="inbox本文")
        _write_adopted_file(notes, "fb-adopted.md", category="scope-escalation", body="adopted本文")
        _write_tbd_file(notes, f"{_FIXED_TIMESTAMP}-001.md", question="q1", answer="")
        _write_tbd_file(notes, f"{_FIXED_TIMESTAMP}-002.md", question="q2", answer="回答あり\n")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["fb", "list"], home=tmp_path)
        assert exc_info.value.code == 0
        default_out = capsys.readouterr().out

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["fb", "list", "--status=active"], home=tmp_path)
        assert exc_info.value.code == 0
        active_out = capsys.readouterr().out

        assert default_out == active_out
        assert "fb-inbox.md: github.com/example/foo [inbox] inbox本文" in default_out
        assert "fb-adopted.md" not in default_out
        assert f"{_FIXED_TIMESTAMP}-001.md" not in default_out
        assert f"{_FIXED_TIMESTAMP}-002.md" in default_out


class TestListFeedbackStatusRejected:
    """listサブコマンド `--status=rejected`: feedbackはrejected配下のみを表示する。"""

    def test_rejected_shows_rejected_only(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """`--status=rejected`指定時、feedback側はrejected配下のみ出力する。"""
        notes = _setup_flag_and_notes(tmp_path)
        _write_feedback_file(notes, "fb-inbox.md", body="in-body")
        rejected_dir = notes / "feedback" / "rejected"
        rejected_dir.mkdir(parents=True, exist_ok=True)
        (rejected_dir / "fb-rejected.md").write_text(
            "---\ntarget_repo: github.com/example/foo\n---\n\nrejected-body\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["fb", "list", "--type=feedback", "--status=rejected"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "fb-inbox.md" not in captured.out
        assert "fb-rejected.md: github.com/example/foo [rejected] rejected-body" in captured.out

    def test_rejected_does_not_affect_tbd(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """`--status=rejected`指定時、tbd側は状態フォルダを持たないため全件出力される。"""
        notes = _setup_flag_and_notes(tmp_path)
        (notes / "tbd" / "inbox").mkdir(parents=True, exist_ok=True)
        _write_tbd_file(notes, f"{_FIXED_TIMESTAMP}-001.md", question="q1", answer="")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["fb", "list", "--status=rejected"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert f"{_FIXED_TIMESTAMP}-001.md" in captured.out


class TestEnableSubcommand:
    """enableサブコマンド: フラグファイル不在時に作成、存在時は冪等。"""

    def test_enable_creates_flag(self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
        """フラグファイルが無い状態でも実行でき、生成される。"""
        flag = tmp_path / ".config" / "agent-toolkit" / "feedback-inbox.enabled"
        assert not flag.exists()

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["fb", "enable"], home=tmp_path)

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
            atk.main(["fb", "enable"], home=tmp_path)

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
            atk.main(["fb", "disable"], home=tmp_path)

        assert exc_info.value.code == 0
        assert not flag.exists()
        captured = capsys.readouterr()
        assert "無効化しました" in captured.out

    def test_disable_idempotent(self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
        """フラグファイルが存在しない場合は無動作で完了する。"""
        with pytest.raises(SystemExit) as exc_info:
            atk.main(["fb", "disable"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "既に無効です" in captured.out


class TestStatusSubcommand:
    """statusサブコマンド: 有効状態をexit codeと出力先で通知する。"""

    def test_status_disabled_when_flag_missing(self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
        """フラグファイル不在時はexit 1で標準エラー出力に無効案内を出力する。"""
        with pytest.raises(SystemExit) as exc_info:
            atk.main(["fb", "status"], home=tmp_path)

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "feedback-inbox機能が無効" in captured.err
        assert captured.out == ""

    def test_status_disabled_when_notes_missing(self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
        """フラグありかつ管理repo root不在時はexit 1で標準エラー出力にディレクトリ不在案内を出力する。"""
        flag = tmp_path / ".config" / "agent-toolkit" / "feedback-inbox.enabled"
        flag.parent.mkdir(parents=True, exist_ok=True)
        flag.touch()

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["fb", "status"], home=tmp_path)

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "フィードバック保存ディレクトリが見つかりません" in captured.err
        assert captured.out == ""

    def test_status_enabled_when_both_present(self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
        """フラグとprivate-notesが両方揃っている場合はexit 0で標準出力に有効案内を出力する。"""
        flag = tmp_path / ".config" / "agent-toolkit" / "feedback-inbox.enabled"
        flag.parent.mkdir(parents=True, exist_ok=True)
        flag.touch()
        (tmp_path / "private-notes").mkdir()

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["fb", "status"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "feedback-inboxは有効" in captured.out
        assert captured.err == ""


def _editor_fake_run(
    action: typing.Callable[[pathlib.Path], int],
    myrepo: pathlib.Path | None = None,
    remote_url: str = "https://github.com/example/myrepo.git",
) -> typing.Callable[..., subprocess.CompletedProcess[Any]]:
    """エディター呼び出し時にactionを実行し戻り値をreturncodeとするsubprocess.run差し替えを返す。

    fake-editor以外のコマンドは終了コード0で成功扱いとする。
    myrepo指定時は`git rev-parse --show-toplevel`にmyrepoを、
    `git -C <myrepo> remote get-url origin`にremote_urlを返す（対象リポジトリはcwdから解決される）。
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

        monkeypatch.setattr(subprocess, "run", _editor_fake_run(write_body, myrepo=myrepo))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["fb", "add"], home=tmp_path, now=_FIXED_DT)

        assert exc_info.value.code == 0
        files = list((notes / "feedback" / "inbox").iterdir())
        assert len(files) == 1
        content = files[0].read_text(encoding="utf-8")
        assert "エディター経由の本文" in content

        captured = capsys.readouterr()
        assert "編集する場合:\n" in captured.out
        assert f"  atk fb edit {files[0].name}\n" in captured.out

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

        monkeypatch.setattr(subprocess, "run", _editor_fake_run(write_blanks, myrepo=myrepo))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["fb", "add"], home=tmp_path, now=_FIXED_DT)

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
            empty: Any = "" if kwargs.get("text") else b""
            if cmd == ["git", "rev-parse", "--show-toplevel"]:
                stdout: Any = f"{myrepo}\n" if kwargs.get("text") else f"{myrepo}\n".encode()
                return subprocess.CompletedProcess(cmd, returncode=0, stdout=stdout, stderr=empty)
            if cmd == ["git", "-C", str(myrepo), "remote", "get-url", "origin"]:
                stdout = (
                    "https://github.com/example/myrepo.git\n"
                    if kwargs.get("text")
                    else b"https://github.com/example/myrepo.git\n"
                )
                return subprocess.CompletedProcess(cmd, returncode=0, stdout=stdout, stderr=empty)
            return subprocess.CompletedProcess(cmd, returncode=0, stdout=empty, stderr=empty)

        monkeypatch.setattr(subprocess, "run", fake_run)

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["fb", "add"], home=tmp_path, now=_FIXED_DT)

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

        monkeypatch.setattr(subprocess, "run", _editor_fake_run(lambda _tmp: 2, myrepo=myrepo))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["fb", "add"], home=tmp_path, now=_FIXED_DT)

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "終了コード2" in captured.err
        assert not list((notes / "feedback" / "inbox").iterdir())

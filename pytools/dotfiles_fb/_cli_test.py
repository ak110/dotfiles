"""pytools.dotfiles_fb._cli のテスト。

同値分割と境界値分析で各サブコマンドの観点を網羅する。
add/list/本文要約切り詰めなど基本サブコマンドの単体テストを集約する。
show系テストは`_cli_show_test.py`、adopt/reject/rm/edit/パストラバーサル拒否は`_cli_mutations_test.py`、
process-loop・リモートURL正規化・リポジトリID解決は`_cli_process_loop_test.py`に分離する。
commit/enable/disable/--source等の拡張機能テストは`_cli_extras_test.py`に分離する。
tbd-add/tbd-list/tbd-edit/tbd-answer/tbd-adoptサブコマンドの単体テストは`_cli_tbd_test.py`に分離する。
tbd関連の共通ヘルパー（`_setup_tbd_env`・`_write_tbd_file`）は本ファイルと`_cli_tbd_test.py`の
双方から使うため本ファイルに残置する。
"""

import datetime
import pathlib
import subprocess
from collections.abc import Callable
from typing import Any

import pytest

from pytools.dotfiles_fb import _cli, _formatters

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
    (notes / "feedback" / "inbox").mkdir(parents=True)
    return notes


def _write_feedback_file(
    notes: pathlib.Path,
    filename: str,
    target_repo: str = "github.com/example/foo",
    body: str = "テスト本文",
) -> pathlib.Path:
    """feedback/inbox配下に1ファイルを書き込み、絶対パスを返す。"""
    inbox_dir = notes / "feedback" / "inbox"
    inbox_dir.mkdir(parents=True, exist_ok=True)
    path = inbox_dir / filename
    path.write_text(
        f"---\ntarget_repo: {target_repo}\n---\n\n{body}\n",
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

        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()
        git_calls: list[_GitCall] = []

        def fake_run(cmd: list[str], *_args: object, **kwargs: object) -> subprocess.CompletedProcess[Any]:
            git_calls.append({"cmd": list(cmd), "kwargs": dict(kwargs)})
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

        repo_path = str(myrepo)
        message = "テストメッセージ"

        with pytest.raises(SystemExit) as exc_info:
            _cli.main(["add", repo_path, message], home=tmp_path, now=_FIXED_DT)

        assert exc_info.value.code == 0

        inbox_dir = notes / "feedback" / "inbox"
        files = sorted(inbox_dir.iterdir())
        assert len(files) == 1

        content = files[0].read_text(encoding="utf-8")
        assert "created:" not in content.split("---\n\n", 1)[0]
        assert "target_repo: github.com/example/myrepo" in content

        body = content.split("---\n\n", 1)[1]
        assert body == message + "\n"

        git_cmds = [c["cmd"] for c in git_calls]
        remote_url_cmd = ["git", "-C", str(myrepo), "remote", "get-url", "origin"]
        assert remote_url_cmd in git_cmds
        pull_idx = git_cmds.index(["git", "pull", "--ff-only"])
        assert git_cmds[pull_idx + 1] == ["git", "add", "feedback"]
        assert git_cmds[pull_idx + 2] == ["git", "commit", "-m", "chore: add 1 feedback item"]
        assert git_cmds[pull_idx + 3] == ["git", "push"]
        for call in git_calls:
            if call["cmd"][:2] != ["git", "-C"]:
                assert call["kwargs"].get("cwd") == notes

        captured = capsys.readouterr()
        assert "1件投入:\n" in captured.out
        assert f"  ~/private-notes/feedback/inbox/{files[0].name}\n" in captured.out
        assert "inbox: 計1件" in captured.out
        assert "編集する場合:\n" in captured.out
        assert f"  dotfiles-fb edit {files[0].name}\n" in captured.out


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

        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()
        git_calls: list[_GitCall] = []

        def fake_run(cmd: list[str], *_args: object, **kwargs: object) -> subprocess.CompletedProcess[Any]:
            git_calls.append({"cmd": list(cmd), "kwargs": dict(kwargs)})
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

        repo_path = str(myrepo)

        with pytest.raises(SystemExit) as exc_info:
            _cli.main(["add", repo_path, "メッセージ1", "メッセージ2"], home=tmp_path, now=_FIXED_DT)

        assert exc_info.value.code == 0

        inbox_dir = notes / "feedback" / "inbox"
        files = sorted(inbox_dir.iterdir())
        assert len(files) == 2
        assert files[0].name == f"{_FIXED_TIMESTAMP}-001.md"
        assert files[1].name == f"{_FIXED_TIMESTAMP}-002.md"

        commit_cmd = [c["cmd"] for c in git_calls if "commit" in c["cmd"]][0]
        assert "chore: add 2 feedback items" in commit_cmd

        captured = capsys.readouterr()
        assert "2件投入:\n" in captured.out
        assert f"  ~/private-notes/feedback/inbox/{files[0].name}\n" in captured.out
        assert f"  ~/private-notes/feedback/inbox/{files[1].name}\n" in captured.out
        assert "inbox: 計2件" in captured.out
        assert "編集する場合:\n" in captured.out
        assert f"  dotfiles-fb edit {files[0].name}\n" in captured.out
        assert f"  dotfiles-fb edit {files[1].name}\n" in captured.out


class TestAddRepoPathExpansion:
    """addサブコマンド: ~プレフィックスのrepo_pathがリモートURLへ正規化されること。"""

    def test_tilde_repo_path_is_expanded(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """~展開後にgit remote get-urlでリモートURLが取得され、target_repoへ書き込まれる。"""
        notes = _setup_flag_and_notes(tmp_path)
        monkeypatch.setenv("HOME", str(tmp_path))

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
            _cli.main(["add", "~/myrepo", "テストメッセージ"], home=tmp_path, now=_FIXED_DT)

        assert exc_info.value.code == 0

        inbox = notes / "feedback" / "inbox"
        files = list(inbox.iterdir())
        assert len(files) == 1
        content = files[0].read_text(encoding="utf-8")
        assert "target_repo: github.com/example/myrepo" in content


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
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            _cli.main(["list"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert captured.out == ""


class TestListSingle:
    """listサブコマンド: 1件のフィードバックを1行で出力する。"""

    def test_single_entry(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """1件のフィードバックがfilename・target_repo・本文冒頭要約のtab区切り1行で出力されること。"""
        notes = _setup_flag_and_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md", target_repo="github.com/example/foo", body="本文1")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            _cli.main(["list"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert captured.out == "# feedback\nfb-001.md\tgithub.com/example/foo\t本文1\n"


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
        """異常frontmatter形式は`(unknown)`のtarget_repo欄として出力される。"""
        del label  # parametrize idのみ
        notes = _setup_flag_and_notes(tmp_path)
        (notes / "feedback" / "inbox" / "malformed.md").write_text(content, encoding="utf-8")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            _cli.main(["list"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert captured.out.startswith("# feedback\nmalformed.md\t(unknown)\t")


class TestListMultipleRepos:
    """listサブコマンド: 複数target_repo混在でも1件1行で全件出力される。"""

    def test_multiple_repos_grouped(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """target_repoが異なる複数のフィードバックがそれぞれ1行で出力される。"""
        notes = _setup_flag_and_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md", target_repo="github.com/example/foo")
        _write_feedback_file(notes, "fb-002.md", target_repo="github.com/example/bar")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            _cli.main(["list"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert captured.out.splitlines() == [
            "# feedback",
            "fb-001.md\tgithub.com/example/foo\tテスト本文",
            "fb-002.md\tgithub.com/example/bar\tテスト本文",
        ]


class TestListTargetRepoFilter:
    """listサブコマンド: --target-repo指定で一致するエントリのみ出力する。"""

    def test_filter_matches_single_group(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """複数target_repo混在でも--target-repo指定値と一致するエントリのみ出力される。"""
        notes = _setup_flag_and_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md", target_repo="github.com/example/foo")
        _write_feedback_file(notes, "fb-002.md", target_repo="github.com/example/bar")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            _cli.main(["list", "--target-repo=github.com/example/foo"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "github.com/example/foo" in captured.out
        assert "github.com/example/bar" not in captured.out

    def test_filter_expands_tilde(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """~プレフィックスのローカルパスがgit remote get-urlで正規化され、対応するエントリが出力される。"""
        notes = _setup_flag_and_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md", target_repo="github.com/example/myrepo")
        monkeypatch.setenv("HOME", str(tmp_path))

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
            _cli.main(["list", "--target-repo=~/myrepo"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert captured.out == "# feedback\nfb-001.md\tgithub.com/example/myrepo\tテスト本文\n"

    def test_filter_no_match_outputs_nothing(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """一致するエントリが存在しない場合、標準出力は空になる。"""
        notes = _setup_flag_and_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md", target_repo="github.com/example/foo")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            _cli.main(["list", "--target-repo=github.com/example/nomatch"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert captured.out == ""


class TestListTypeFilter:
    """listサブコマンド: --typeでfeedback/tbd出力を絞り込む。"""

    def test_type_feedback_outputs_only_feedback_section(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """--type=feedback指定時はfeedback部のみ出力されtbdヘッダは出力されない。"""
        notes = _setup_flag_and_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md", target_repo="github.com/example/foo", body="本文1")
        _write_tbd_file(notes, f"{_FIXED_TIMESTAMP}-001.md", question="q1")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            _cli.main(["list", "--type=feedback"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert captured.out == "# feedback\nfb-001.md\tgithub.com/example/foo\t本文1\n"

    def test_type_tbd_outputs_status_label(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """--type=tbd指定時はtbd部のみ出力され回答状況ラベルが付与される。"""
        notes = _setup_flag_and_notes(tmp_path)
        _write_tbd_file(notes, f"{_FIXED_TIMESTAMP}-001.md", question="q1", answer="")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            _cli.main(["list", "--type=tbd"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert captured.out == f"# tbd\n{_FIXED_TIMESTAMP}-001.md\tgithub.com/example/foo\t[unanswered] q1\n"

    def test_type_all_omits_empty_section_header(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """--type=all（既定）でtbd側が0件の場合はtbd種別ヘッダを省略する。"""
        notes = _setup_flag_and_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md", body="本文1")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            _cli.main(["list"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "# feedback" in captured.out
        assert "# tbd" not in captured.out


class TestListSkipPull:
    """listサブコマンド: --skip-pull指定時はgit pullをスキップする。"""

    def test_skip_pull_omits_git_pull(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """--skip-pull指定時はgit pull --ff-onlyが実行されない。"""
        notes = _setup_flag_and_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            _cli.main(["list", "--skip-pull"], home=tmp_path)

        assert exc_info.value.code == 0
        assert not any(c["cmd"][:2] == ["git", "pull"] for c in git_calls)


class TestBodySummaryTruncation:
    """_body_summary: 40文字境界での切り詰め動作を検証する。"""

    def test_exactly_40_chars_not_truncated(self) -> None:
        """本文冒頭行がちょうど40文字の場合は切り詰めず`...`を付与しない。"""
        text = "---\ntarget_repo: github.com/example/foo\n---\n\n" + "あ" * 40 + "\n"
        assert _formatters._body_summary(text) == "あ" * 40  # pylint: disable=protected-access  # noqa: SLF001

    def test_over_40_chars_truncated_with_ellipsis(self) -> None:
        """本文冒頭行が40文字を超える場合は40文字で切り詰め`...`を付与する。"""
        text = "---\ntarget_repo: github.com/example/foo\n---\n\n" + "い" * 41 + "\n"
        assert _formatters._body_summary(text) == "い" * 40 + "..."  # pylint: disable=protected-access  # noqa: SLF001

    def test_multiline_body_uses_first_line_only(self) -> None:
        """本文が複数行の場合は先頭行のみを要約対象とし改行以降は無視する。"""
        text = "---\ntarget_repo: github.com/example/foo\n---\n\n先頭行\n2行目\n"
        assert _formatters._body_summary(text) == "先頭行"  # pylint: disable=protected-access  # noqa: SLF001


def _setup_tbd_env(tmp_path: pathlib.Path) -> pathlib.Path:
    """フラグファイルとprivate-notes・tbd/inboxディレクトリを準備する。"""
    flag = tmp_path / ".config" / "agent-toolkit" / "feedback-inbox.enabled"
    flag.parent.mkdir(parents=True, exist_ok=True)
    flag.touch()
    notes = tmp_path / "private-notes"
    (notes / "tbd" / "inbox").mkdir(parents=True)
    return notes


def _write_tbd_file(
    notes: pathlib.Path,
    filename: str,
    target_repo: str = "github.com/example/foo",
    question: str = "テスト質問",
    answer: str = "",
) -> pathlib.Path:
    """tbd/inbox配下に1ファイルを書き込み、絶対パスを返す。"""
    tbd_dir = notes / "tbd" / "inbox"
    tbd_dir.mkdir(parents=True, exist_ok=True)
    path = tbd_dir / filename
    path.write_text(
        f"---\ncreated: {_FIXED_ISO}\ntarget_repo: {target_repo}\nquestion_type: free\n---\n\n"
        f"## 質問\n\n{question}\n\n## 回答\n\n{answer}",
        encoding="utf-8",
    )
    return path

"""atk (agent-toolkit `atk fb`) のテスト。

同値分割と境界値分析で各サブコマンドの観点を網羅する。
add・本文要約切り詰めなど基本サブコマンドの単体テストを集約する。
list系は`_atk_fb_list_test.py`、show系は`_atk_fb_show_test.py`、mutation系は`_atk_fb_mutations_test.py`、
process-loop・リポジトリ解決は`_atk_fb_process_loop_test.py`、拡張機能は`_atk_fb_extras_test.py`、
TBD系は`_atk_fb_tbd_test.py`、本文要約の切り詰め境界ケースは`_atk_fb_formatters_test.py`に分離する。
TBD共通ヘルパーは本ファイルと分割先テストの双方から使うため本ファイルに残置する。
gitリモート応答フェイクは複数テストファイルが共有するため`_atk_git_fake_test_helpers.py`に集約する。
"""

import datetime
import pathlib
import subprocess
import sys
from collections.abc import Callable
from typing import Any

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import _atk_fb_add as _add  # noqa: E402  # pylint: disable=wrong-import-position
import atk  # noqa: E402  # pylint: disable=wrong-import-position

# pylint: disable-next=wrong-import-position,import-error
from _atk_git_fake_test_helpers import make_git_remote_fake as _make_git_remote_fake  # noqa: E402

_GitCall = dict[str, Any]

_FIXED_DT = datetime.datetime(2024, 1, 15, 10, 30, 0)
_FIXED_TIMESTAMP = _FIXED_DT.strftime("%Y%m%d-%H%M%S")
_FIXED_ISO = _FIXED_DT.isoformat()

# 端末幅の固定化は`conftest.py`の`_fixed_terminal_size`autouseフィクスチャへ集約する
# （`shutil`モジュール差し替えのため個別テストファイルへの重複定義は不要）。


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
    source: str | None = None,
) -> pathlib.Path:
    """feedback/inbox配下に1ファイルを書き込み、絶対パスを返す。`source`指定時はfrontmatterへ追記する。"""
    inbox_dir = notes / "feedback" / "inbox"
    inbox_dir.mkdir(parents=True, exist_ok=True)
    path = inbox_dir / filename
    source_line = f"source: {source}\n" if source is not None else ""
    path.write_text(
        f"---\ntarget_repo: {target_repo}\n{source_line}---\n\n{body}\n",
        encoding="utf-8",
    )
    return path


class TestMutationTargetRepoParserOption:
    """mutation系サブコマンドの`--target-repo`受理をargparseレベルで検証する。"""

    @pytest.mark.parametrize(
        ("top_command", "subcommand", "argv_tail"),
        [
            ("fb", "adopt", ["20260714-000001-001.md"]),
            ("fb", "reject", ["20260714-000001-001.md"]),
            ("fb", "rm", ["20260714-000001-001.md"]),
            ("fb", "edit", ["20260714-000001-001.md"]),
            ("fb", "start-processing", ["20260714-000001-001.md"]),
            ("tb", "edit", ["20260714-000001-001.md"]),
            ("tb", "adopt", ["20260714-000001-001.md"]),
            ("tb", "rm", ["20260714-000001-001.md"]),
        ],
    )
    def test_accepts_target_repo(self, top_command: str, subcommand: str, argv_tail: list[str]) -> None:
        """8種のmutation系サブコマンドすべてが`--target-repo`を受理する。"""
        parser = atk._build_parser()  # pylint: disable=protected-access  # noqa: SLF001
        args = parser.parse_args([top_command, subcommand, "--target-repo", "github.com/foo/bar", *argv_tail])
        assert args.target_repo == "github.com/foo/bar"

    def test_commit_has_no_target_repo_option(self) -> None:
        """`commit`は引数を取らないシグネチャのため`--target-repo`を受理しない。"""
        parser = atk._build_parser()  # pylint: disable=protected-access  # noqa: SLF001
        with pytest.raises(SystemExit) as exc_info:
            parser.parse_args(["fb", "commit", "--target-repo", "github.com/foo/bar"])
        assert exc_info.value.code == 2


class TestTbdAddSourceOptionParser:
    """tb addサブコマンドの`--source`受理をargparseレベルで検証する。"""

    def test_accepts_source(self) -> None:
        """`tb add`が`--source`を受理しargs.sourceへ格納される。"""
        parser = atk._build_parser()  # pylint: disable=protected-access  # noqa: SLF001
        args = parser.parse_args(["tb", "add", "--source", "session-hold", "hello"])
        assert args.source == "session-hold"


class TestAddTargetRepoOptionParser:
    """`fb add`・`tb add`の`--target-repo`受理をargparseレベルで検証する。"""

    def test_fb_add_accepts_target_repo(self) -> None:
        """`fb add`が`--target-repo`を受理しargs.target_repoへ格納される。"""
        parser = atk._build_parser()  # pylint: disable=protected-access  # noqa: SLF001
        args = parser.parse_args(["fb", "add", "--target-repo", "github.com/foo/bar", "本文"])
        assert args.target_repo == "github.com/foo/bar"

    def test_tb_add_accepts_target_repo(self) -> None:
        """`tb add`が`--target-repo`を受理しargs.target_repoへ格納される。"""
        parser = atk._build_parser()  # pylint: disable=protected-access  # noqa: SLF001
        args = parser.parse_args(["tb", "add", "--target-repo", "github.com/foo/bar", "本文"])
        assert args.target_repo == "github.com/foo/bar"


class TestSubcommandSubparserDefault:
    """`fb add`・`tb add`が`args.subparser`へ自パーサ参照を設定することを検証する。"""

    def test_fb_add(self) -> None:
        """`fb add`解析後、`args.subparser.prog`が`atk fb add`になる。"""
        args = atk._build_parser().parse_args(["fb", "add", "本文"])  # pylint: disable=protected-access  # noqa: SLF001
        assert args.subparser.prog == "atk fb add"

    def test_tb_add(self) -> None:
        """`tb add`解析後、`args.subparser.prog`が`atk tb add`になる。"""
        args = atk._build_parser().parse_args(["tb", "add", "本文"])  # pylint: disable=protected-access  # noqa: SLF001
        assert args.subparser.prog == "atk tb add"


class TestSpaceSeparatedOptionWarning:
    """mainがparse前に空白区切りオプションを警告することを検証する。"""

    @pytest.mark.parametrize(
        "top_command,subcommand",
        [("fb", "adopt"), ("fb", "reject"), ("tb", "adopt")],
    )
    def test_warns_before_argument_error(self, top_command: str, subcommand: str, capsys: pytest.CaptureFixture[str]) -> None:
        with pytest.raises(SystemExit):
            atk.main([top_command, subcommand, "missing.md", "--note", "memo"])
        assert "警告: --noteは--note=VALUE形式で渡すことを推奨します。" in capsys.readouterr().err

    def test_does_not_warn_for_equals_form(self, capsys: pytest.CaptureFixture[str]) -> None:
        with pytest.raises(SystemExit):
            atk.main(["fb", "adopt", "missing.md", "--note=memo"])
        assert "警告:" not in capsys.readouterr().err


class TestUnansweredTbdNotification:
    """非TBDサブコマンド完了後の未回答TBD通知を検証する。"""

    @pytest.mark.parametrize("count", [0, 1, 3])
    def test_notifies_unanswered_entries_after_non_tbd_command(
        self, count: int, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        notes = _setup_tbd_env(tmp_path)
        for index in range(count):
            _write_tbd_file(notes, f"tbd-{index:03d}.md", question=f"質問{index}")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))
        with pytest.raises(SystemExit) as exc_info:
            atk.main(["fb", "list", "--type=feedback", "--skip-pull"], home=tmp_path)
        assert exc_info.value.code == 0
        stderr = capsys.readouterr().err
        assert stderr.count("[unanswered]") == count
        assert stderr.startswith("# tbd\n") if count else not stderr


class TestFlagFileMissing:
    """フラグファイル不在時にexit 1とstderr案内を返すこと。"""

    def test_exits_with_error(self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
        """フラグファイルが存在しない場合はexit 1でstderrに案内を出力する。"""
        with pytest.raises(SystemExit) as exc_info:
            atk.main(["fb", "add", str(tmp_path / "myrepo"), "dummy message"], home=tmp_path)

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "feedback-inbox機能が無効" in captured.err


class TestPrivateNotesMissing:
    """管理repo root不在時にexit 1とディレクトリ不在案内を返すこと。"""

    def test_exits_with_directory_missing_guide(self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
        """管理repo rootが存在しない場合はexit 1でディレクトリ不在案内を出力する。"""
        flag = tmp_path / ".config" / "agent-toolkit" / "feedback-inbox.enabled"
        flag.parent.mkdir(parents=True, exist_ok=True)
        flag.touch()

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["fb", "add", str(tmp_path / "myrepo"), "dummy message"], home=tmp_path)

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "フィードバック保存ディレクトリが見つかりません" in captured.err


class TestNoSubcommand:
    """サブコマンド未指定時にargparse由来のexit 2が発生すること。"""

    def test_exits_with_usage_error(self) -> None:
        """サブコマンド未指定の場合はexit 2でSystemExitが発生する。"""
        with pytest.raises(SystemExit) as exc_info:
            atk.main([])

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
            atk.main(["fb", "add", repo_path, message], home=tmp_path, now=_FIXED_DT)

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
        assert f"  atk fb edit {files[0].name}\n" in captured.out


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
            atk.main(["fb", "add", repo_path, "メッセージ1", "メッセージ2"], home=tmp_path, now=_FIXED_DT)

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
        assert f"  atk fb edit {files[0].name}\n" in captured.out
        assert f"  atk fb edit {files[1].name}\n" in captured.out


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

        monkeypatch.setattr(subprocess, "run", _make_git_remote_fake(myrepo))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["fb", "add", "~/myrepo", "テストメッセージ"], home=tmp_path, now=_FIXED_DT)

        assert exc_info.value.code == 0

        inbox = notes / "feedback" / "inbox"
        files = list(inbox.iterdir())
        assert len(files) == 1
        content = files[0].read_text(encoding="utf-8")
        assert "target_repo: github.com/example/myrepo" in content


class TestParseLeadingFrontmatter:
    """_parse_leading_frontmatterの単体テスト（frontmatter判定と本文分離を検証する）。"""

    def test_leading_frontmatter_overrides_target_repo(self) -> None:
        """先頭frontmatterの`target_repo`がパース結果に含まれる。"""
        message = "---\ntarget_repo: github.com/x/y\n---\n\n本文"
        fm, body = _add._parse_leading_frontmatter(message)  # pylint: disable=protected-access  # noqa: SLF001
        assert fm == {"target_repo": "github.com/x/y"}
        assert body == "本文"

    def test_leading_frontmatter_source_priority(self) -> None:
        """先頭frontmatterに`source`が含まれる場合はパース結果に含まれる。"""
        message = "---\ntarget_repo: github.com/x/y\nsource: session-review\n---\n\n本文"
        fm, body = _add._parse_leading_frontmatter(message)  # pylint: disable=protected-access  # noqa: SLF001
        assert fm == {"target_repo": "github.com/x/y", "source": "session-review"}
        assert body == "本文"

    def test_without_frontmatter_returns_original(self) -> None:
        """先頭がfrontmatterでない場合は空dictと元メッセージを返す。"""
        message = "普通の本文\n2行目"
        fm, body = _add._parse_leading_frontmatter(message)  # pylint: disable=protected-access  # noqa: SLF001
        assert not fm
        assert body == message

    def test_body_horizontal_rule_not_treated_as_frontmatter(self) -> None:
        """本文中の水平線があるメッセージはfrontmatterと解釈せず本文が保持される。"""
        message = "---\n\n本文開始\n\n---\n\n本文継続"
        fm, body = _add._parse_leading_frontmatter(message)  # pylint: disable=protected-access  # noqa: SLF001
        assert not fm
        assert body == message

    def test_frontmatter_without_closing_returns_original(self) -> None:
        """先頭が3ハイフンで始まっても閉じ区切りがない場合は元メッセージを返す。"""
        message = "---\ntarget_repo: github.com/x/y\n本文継続なし"
        fm, body = _add._parse_leading_frontmatter(message)  # pylint: disable=protected-access  # noqa: SLF001
        assert not fm
        assert body == message


class TestAddFrontmatterOverride:
    """addサブコマンド: メッセージ先頭のfrontmatterがCLIオプションより優先されること。"""

    def test_message_frontmatter_overrides_cli_target_repo(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """メッセージ先頭frontmatterの`target_repo`がCLIオプションより優先される。"""
        notes = _setup_flag_and_notes(tmp_path)
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()

        monkeypatch.setattr(subprocess, "run", _make_git_remote_fake(myrepo))

        message = "---\ntarget_repo: github.com/other/repo\nsource: session-review\n---\n\nテスト本文"

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["fb", "add", str(myrepo), message, "--source", "cli-source"], home=tmp_path, now=_FIXED_DT)
        assert exc_info.value.code == 0

        inbox = notes / "feedback" / "inbox"
        files = list(inbox.iterdir())
        assert len(files) == 1
        content = files[0].read_text(encoding="utf-8")
        assert "target_repo: github.com/other/repo" in content
        assert "source: session-review" in content
        body = content.split("---\n\n", 1)[1]
        assert body == "テスト本文\n"

    def test_multiple_messages_mixed_frontmatter(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """複数メッセージ混在時（一部のみfrontmatter付き）に各メッセージが独立判定される。"""
        notes = _setup_flag_and_notes(tmp_path)
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()

        monkeypatch.setattr(subprocess, "run", _make_git_remote_fake(myrepo))

        msg_with_fm = "---\ntarget_repo: github.com/override/repo\n---\n\nfm付き本文"
        msg_plain = "frontmatter無し本文"

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["fb", "add", str(myrepo), msg_with_fm, msg_plain], home=tmp_path, now=_FIXED_DT)
        assert exc_info.value.code == 0

        inbox = notes / "feedback" / "inbox"
        files = sorted(inbox.iterdir())
        assert len(files) == 2
        content_first = files[0].read_text(encoding="utf-8")
        content_second = files[1].read_text(encoding="utf-8")
        assert "target_repo: github.com/override/repo" in content_first
        assert content_first.split("---\n\n", 1)[1] == "fm付き本文\n"
        assert "target_repo: github.com/example/myrepo" in content_second
        assert content_second.split("---\n\n", 1)[1] == msg_plain + "\n"

    def test_frontmatter_target_repo_only_falls_back_to_cli_source(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """frontmatterに`target_repo`のみで`source`未指定の場合、CLIオプション値を採用する。"""
        notes = _setup_flag_and_notes(tmp_path)
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()

        monkeypatch.setattr(subprocess, "run", _make_git_remote_fake(myrepo))

        message = "---\ntarget_repo: github.com/other/repo\n---\n\n本文"

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["fb", "add", str(myrepo), message, "--source", "cli-source"], home=tmp_path, now=_FIXED_DT)
        assert exc_info.value.code == 0

        inbox = notes / "feedback" / "inbox"
        files = list(inbox.iterdir())
        assert len(files) == 1
        content = files[0].read_text(encoding="utf-8")
        assert "target_repo: github.com/other/repo" in content
        assert "source: cli-source" in content


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
    source: str | None = None,
) -> pathlib.Path:
    """tbd/inbox配下に1ファイルを書き込み、絶対パスを返す。`source`指定時はfrontmatterへ追記する。"""
    tbd_dir = notes / "tbd" / "inbox"
    tbd_dir.mkdir(parents=True, exist_ok=True)
    path = tbd_dir / filename
    source_line = f"source: {source}\n" if source is not None else ""
    path.write_text(
        f"---\ncreated: {_FIXED_ISO}\ntarget_repo: {target_repo}\nquestion_type: free\n{source_line}---\n\n"
        f"## 質問\n\n{question}\n\n## 回答\n\n{answer}",
        encoding="utf-8",
    )
    return path

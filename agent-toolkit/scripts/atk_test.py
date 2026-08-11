"""atk (agent-toolkit `atk mq`) のテスト。

同値分割と境界値分析で各サブコマンドの観点を網羅する。
add・本文要約切り詰めなど基本サブコマンドの単体テストを集約する。
list系は`_atk_mq_list_test.py`、show系は`_atk_mq_show_test.py`、mutation系は`_atk_mq_mutations_test.py`、
process-loop・リポジトリ解決は`_atk_mq_process_loop_test.py`、拡張機能は`_atk_mq_extras_test.py`、
TBD系は`_atk_mq_tbd_test.py`、本文要約の切り詰め境界ケースは`_atk_mq_formatters_test.py`に分離する。
TBD共通ヘルパーは本ファイルと分割先テストの双方から使うため本ファイルに残置する。
gitリモート応答フェイクは複数テストファイルが共有するため`_atk_git_fake_test_helpers.py`に集約する。
"""

import datetime
import pathlib
import subprocess
import sys
import types
from collections.abc import Callable
from typing import Any

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import _atk_mq_add as _add  # noqa: E402  # pylint: disable=wrong-import-position
import atk  # noqa: E402  # pylint: disable=wrong-import-position
from _atk_git_fake_test_helpers import (  # noqa: E402  # pylint: disable=wrong-import-position,import-error
    _FIXED_HEAD_COMMIT,
)
from _atk_git_fake_test_helpers import (  # noqa: E402  # pylint: disable=wrong-import-position,import-error
    make_git_remote_fake as _make_git_remote_fake,
)

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


def _setup_notes(tmp_path: pathlib.Path) -> pathlib.Path:
    """private-notesディレクトリを準備する。"""
    notes = tmp_path / "private-notes"
    notes.mkdir()
    (notes / "inbox").mkdir(parents=True)
    return notes


def _write_feedback_file(
    notes: pathlib.Path,
    filename: str,
    target_repo: str = "github.com/example/foo",
    body: str = "テスト本文",
    source: str | None = None,
) -> pathlib.Path:
    """inbox配下に1ファイルを書き込み、絶対パスを返す。`source`指定時はfrontmatterへ追記する。"""
    inbox_dir = notes / "inbox"
    inbox_dir.mkdir(parents=True, exist_ok=True)
    path = inbox_dir / filename
    source_line = f"source: {source}\n" if source is not None else ""
    path.write_text(
        f"---\ntarget_repo: {target_repo}\ntype: feedback\n{source_line}---\n\n{body}\n",
        encoding="utf-8",
    )
    return path


class TestMutationTargetRepoParserOption:
    """mutation系サブコマンドの`--target-repo`受理をargparseレベルで検証する。"""

    @pytest.mark.parametrize(
        ("top_command", "subcommand", "argv_tail"),
        [
            ("mq", "adopt", ["20260714-000001-001.md"]),
            ("mq", "reject", ["20260714-000001-001.md"]),
            ("mq", "rm", ["20260714-000001-001.md"]),
            ("mq", "edit", ["20260714-000001-001.md"]),
            ("mq", "start-processing", ["20260714-000001-001.md"]),
            ("mq", "return-to-inbox", ["20260714-000001-001.md"]),
        ],
    )
    def test_accepts_target_repo(self, top_command: str, subcommand: str, argv_tail: list[str]) -> None:
        """6種のmutation系サブコマンドすべてが`--target-repo`を受理する。"""
        parser = atk._build_parser()  # pylint: disable=protected-access  # noqa: SLF001
        args = parser.parse_args([top_command, subcommand, "--target-repo", "github.com/foo/bar", *argv_tail])
        assert args.target_repo == "github.com/foo/bar"

    def test_edit_accepts_message(self) -> None:
        """`edit FILENAME MESSAGE`を解析して両方の位置引数を保持する。"""
        parser = atk._build_parser()  # pylint: disable=protected-access  # noqa: SLF001
        args = parser.parse_args(["mq", "edit", "20260714-000001-001.md", "更新本文"])
        assert args.filename == "20260714-000001-001.md"
        assert args.message == "更新本文"

    @pytest.mark.parametrize("value", ["2", "3.5", "three"])
    def test_return_to_inbox_rejects_invalid_cooldown_days(self, value: str) -> None:
        """再処理抑制日数は3以上の整数だけを受理する。"""
        parser = atk._build_parser()  # pylint: disable=protected-access  # noqa: SLF001
        with pytest.raises(SystemExit):
            parser.parse_args(["mq", "return-to-inbox", "entry.md", f"--cooldown-days={value}"])

    def test_return_to_inbox_accepts_minimum_cooldown_days(self) -> None:
        """再処理抑制日数の下限3を受理する。"""
        parser = atk._build_parser()  # pylint: disable=protected-access  # noqa: SLF001
        args = parser.parse_args(["mq", "return-to-inbox", "entry.md", "--cooldown-days=3"])
        assert args.cooldown_days == 3

    def test_edit_without_message_remains_interactive(self) -> None:
        """従来の`edit FILENAME`ではMESSAGEを未指定として扱う。"""
        parser = atk._build_parser()  # pylint: disable=protected-access  # noqa: SLF001
        args = parser.parse_args(["mq", "edit", "20260714-000001-001.md"])
        assert args.filename == "20260714-000001-001.md"
        assert args.message is None

    def test_commit_has_no_target_repo_option(self) -> None:
        """`commit`は引数を取らないシグネチャのため`--target-repo`を受理しない。"""
        parser = atk._build_parser()  # pylint: disable=protected-access  # noqa: SLF001
        with pytest.raises(SystemExit) as exc_info:
            parser.parse_args(["mq", "commit", "--target-repo", "github.com/foo/bar"])
        assert exc_info.value.code == 2


def test_convert_to_plan_parser_accepts_repeated_dependencies() -> None:
    """convert-to-planが必須計画と複数の依存先を保持する。"""
    parser = atk._build_parser()  # pylint: disable=protected-access  # noqa: SLF001
    args = parser.parse_args(
        [
            "mq",
            "convert-to-plan",
            "feedback.md",
            "--plan-file",
            "/tmp/plan.md",
            "--depends-on",
            "first.md",
            "--depends-on",
            "second.md",
        ]
    )
    assert args.filename == "feedback.md"
    assert args.plan_file == "/tmp/plan.md"
    assert args.depends_on == ["first.md", "second.md"]


def test_set_dependencies_parser_accepts_repeated_dependencies() -> None:
    """set-dependenciesが既存feedbackと複数の依存先を受理する。"""
    parser = atk._build_parser()  # pylint: disable=protected-access  # noqa: SLF001
    args = parser.parse_args(["mq", "set-dependencies", "feedback.md", "--depends-on", "first", "--depends-on", "second.md"])
    assert args.filename == "feedback.md"
    assert args.depends_on == ["first", "second.md"]


class TestTbdAddSourceOptionParser:
    """TBD投入時の`--source`受理をargparseレベルで検証する。"""

    def test_accepts_source(self) -> None:
        """`mq add --type=tbd`が`--source`を受理しargs.sourceへ格納される。"""
        parser = atk._build_parser()  # pylint: disable=protected-access  # noqa: SLF001
        args = parser.parse_args(["mq", "add", "--type=tbd", "--source", "session-hold", "hello"])
        assert args.source == "session-hold"


class TestServeParser:
    """serveトップレベルサブコマンドの引数境界を検証する。"""

    def test_defaults_and_explicit_values(self) -> None:
        """host/portの省略値と明示値を保持する。"""
        parser = atk._build_parser()  # pylint: disable=protected-access  # noqa: SLF001
        defaults = parser.parse_args(["serve"])
        explicit = parser.parse_args(["serve", "--host", "0.0.0.0", "--port", "65535"])
        assert (defaults.host, defaults.port) == (None, None)
        assert (explicit.host, explicit.port) == ("0.0.0.0", 65535)

    @pytest.mark.parametrize("port", ["0", "65536"])
    def test_rejects_out_of_range_port(self, port: str) -> None:
        """port範囲外をargparseエラーにする。"""
        parser = atk._build_parser()  # pylint: disable=protected-access  # noqa: SLF001
        with pytest.raises(SystemExit) as error:
            parser.parse_args(["serve", "--port", port])
        assert error.value.code == 2

    def test_dispatches_resolved_arguments(self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
        """mainがhost・port・homeをserve起動層へ渡して正常終了する。"""
        calls: list[dict[str, object]] = []

        def run(*, host: str | None, port: int | None, home: pathlib.Path) -> None:
            calls.append({"host": host, "port": port, "home": home})

        serve = types.ModuleType("_atk_serve")
        serve.__dict__["run"] = run
        monkeypatch.setitem(sys.modules, "_atk_serve", serve)
        with pytest.raises(SystemExit) as error:
            atk.main(["serve", "--host", "127.0.0.2", "--port", "28766"], home=tmp_path)
        assert error.value.code == 0
        assert calls == [{"host": "127.0.0.2", "port": 28766, "home": tmp_path}]

    def test_non_serve_import_does_not_load_serve_dependencies(self) -> None:
        """fresh processでatkを読み込んでもserve実装を解決しない。"""
        script_dir = pathlib.Path(atk.__file__).resolve().parent
        code = (
            "import sys; "
            f"sys.path.insert(0, {str(script_dir)!r}); "
            "import atk; "
            "raise SystemExit(1 if '_atk_serve' in sys.modules else 0)"
        )

        result = subprocess.run([sys.executable, "-c", code], check=False)

        assert result.returncode == 0


class TestAddTargetRepoOptionParser:
    """`mq add`の`--target-repo`受理をargparseレベルで検証する。"""

    @pytest.mark.parametrize("type_option", [[], ["--type=tbd"]])
    def test_add_accepts_target_repo(self, type_option: list[str]) -> None:
        """`mq add`が種別にかかわらず`--target-repo`を受理する。"""
        parser = atk._build_parser()  # pylint: disable=protected-access  # noqa: SLF001
        args = parser.parse_args(["mq", "add", *type_option, "--target-repo", "github.com/foo/bar", "本文"])
        assert args.target_repo == "github.com/foo/bar"

    def test_add_help_describes_explicit_worktree_resolution(self, capsys: pytest.CaptureFixture[str]) -> None:
        """addの案内がREPO_PATHとtarget_repo指定の解決先を区別する。"""
        parser = atk._build_parser()  # pylint: disable=protected-access  # noqa: SLF001

        with pytest.raises(SystemExit) as exc_info:
            parser.parse_args(["mq", "add", "--help"])

        assert exc_info.value.code == 0
        output = capsys.readouterr().out
        assert "ローカルパス指定時に指定worktree" in output
        assert "正規化リモートURL指定時にローカルHEADを持たない" in output
        assert "計画作成に用いた正確なworktreeを指定し" in output


def test_add_output_reloads_saved_metadata(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """add完了表示が保存済みfrontmatterの照合対象を列挙する。"""
    _setup_notes(tmp_path)
    myrepo = tmp_path / "myrepo"
    myrepo.mkdir()
    monkeypatch.setattr(subprocess, "run", _make_git_remote_fake(myrepo))

    with pytest.raises(SystemExit) as exc_info:
        atk.main(["mq", "add", str(myrepo), "本文"], home=tmp_path, now=_FIXED_DT)

    assert exc_info.value.code == 0
    output = capsys.readouterr().out
    assert "target_repo: github.com/example/myrepo" in output
    assert f"target_commit: {_FIXED_HEAD_COMMIT}" in output
    assert "plan_file: なし" in output
    assert "depends_on: なし" in output


class TestSubcommandSubparserDefault:
    """`mq add`が`args.subparser`へ自パーサ参照を設定することを検証する。"""

    @pytest.mark.parametrize("type_option", [[], ["--type=tbd"]])
    def test_add(self, type_option: list[str]) -> None:
        """`mq add`解析後は種別にかかわらず同じサブパーサを保持する。"""
        args = atk._build_parser().parse_args(  # pylint: disable=protected-access  # noqa: SLF001
            ["mq", "add", *type_option, "本文"]
        )
        assert args.subparser.prog == "atk mq add"


class TestSpaceSeparatedOptionWarning:
    """mainがparse前に空白区切りオプションを警告することを検証する。"""

    @pytest.mark.parametrize(
        "top_command,subcommand",
        [("mq", "adopt"), ("mq", "reject"), ("mq", "adopt")],
    )
    def test_warns_before_argument_error(self, top_command: str, subcommand: str, capsys: pytest.CaptureFixture[str]) -> None:
        with pytest.raises(SystemExit):
            atk.main([top_command, subcommand, "missing.md", "--note", "memo"])
        assert "警告: --noteは--note=VALUE形式で渡すことを推奨します。" in capsys.readouterr().err

    def test_does_not_warn_for_equals_form(self, capsys: pytest.CaptureFixture[str]) -> None:
        with pytest.raises(SystemExit):
            atk.main(["mq", "adopt", "missing.md", "--note=memo"])
        assert "警告:" not in capsys.readouterr().err


class TestUnansweredTbdNotification:
    """`list`・`show`が通知対象の未回答TBDを全て含む場合は通知を抑止し、
    そうでない場合は通知が表示されることを検証する。"""

    @pytest.mark.parametrize("count", [0, 1, 3])
    def test_notifies_unanswered_entries_after_non_tbd_command(
        self, count: int, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        notes = _setup_notes(tmp_path)
        for index in range(count):
            _write_tbd_file(notes, f"tbd-{index:03d}.md", question=f"質問{index}")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))
        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "list", "--type=feedback", "--skip-pull"], home=tmp_path)
        assert exc_info.value.code == 0
        stderr = capsys.readouterr().err
        assert stderr.count("[inbox/unanswered]") == count
        assert stderr.startswith("# tbd\n") if count else not stderr

    def test_suppresses_notify_when_list_covers_all_unanswered(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """list --type=tbd --answered=no実行時、通知が抑止されることを検証する。"""
        notes = _setup_notes(tmp_path)
        _write_tbd_file(notes, f"{_FIXED_TIMESTAMP}-001.md", question="q1", answer="")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))
        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "list", "--type=tbd", "--answered=no", "--skip-pull"], home=tmp_path)
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert captured.out.count("[inbox/unanswered]") == 1
        assert "[inbox/unanswered]" not in captured.err

    def test_suppresses_notify_when_list_covers_all_unanswered_with_defaults(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """list --type=all --status=active --answered=all実行時、通知が抑止されることを検証する。"""
        notes = _setup_notes(tmp_path)
        _write_tbd_file(notes, f"{_FIXED_TIMESTAMP}-001.md", question="q1", answer="")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))
        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "list", "--skip-pull"], home=tmp_path)
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert captured.out.count("[inbox/unanswered]") == 1
        assert "[inbox/unanswered]" not in captured.err

    def test_does_not_suppress_notify_when_list_has_source_filter(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """list --source指定時、通知が抑止されないことを検証する。"""
        notes = _setup_notes(tmp_path)
        _write_tbd_file(notes, f"{_FIXED_TIMESTAMP}-001.md", question="q1", answer="")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))
        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "list", "--source=session-review", "--skip-pull"], home=tmp_path)
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "# tbd" in captured.err

    def test_does_not_suppress_notify_when_list_has_category_filter(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """list --category指定時、通知が抑止されないことを検証する。"""
        notes = _setup_notes(tmp_path)
        _write_tbd_file(notes, f"{_FIXED_TIMESTAMP}-001.md", question="q1", answer="")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))
        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "list", "--category=test", "--skip-pull"], home=tmp_path)
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "# tbd" in captured.err

    def test_does_not_suppress_notify_when_list_has_status_inbox(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """list --status=inbox単独では通知が抑止されないことを検証する。"""
        notes = _setup_notes(tmp_path)
        _write_tbd_file(notes, f"{_FIXED_TIMESTAMP}-001.md", question="q1", answer="")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))
        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "list", "--status=inbox", "--skip-pull"], home=tmp_path)
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "# tbd" in captured.err

    def test_suppresses_notify_when_show_all_covers_unanswered(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """show --all --type=tbd --answered=no実行時、通知が抑止されることを検証する。"""
        notes = _setup_notes(tmp_path)
        _write_tbd_file(notes, f"{_FIXED_TIMESTAMP}-001.md", question="q1", answer="")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))
        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "show", "--all", "--type=tbd", "--answered=no", "--skip-pull"], home=tmp_path)
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "# tbd" not in captured.err

    def test_does_not_suppress_notify_when_show_with_filename(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """show <FILENAME>（単一ファイル指定）実行時、通知が抑止されないことを検証する。"""
        notes = _setup_notes(tmp_path)
        _write_tbd_file(notes, f"{_FIXED_TIMESTAMP}-001.md", question="q1", answer="")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))
        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "show", f"{_FIXED_TIMESTAMP}-001.md", "--skip-pull"], home=tmp_path)
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "# tbd" in captured.err


class TestInboxAlwaysEnabled:
    """inbox常時有効化: フラグファイル不在でもprivate-notesさえ揃えば通常どおり動作すること。"""

    @pytest.mark.parametrize("subcommand", ["enable", "disable", "status"])
    def test_removed_control_subcommands_exit_with_usage_error(self, subcommand: str) -> None:
        """削除済みのinbox制御サブコマンドはexit 2で拒否される。"""
        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", subcommand])

        assert exc_info.value.code == 2

    def test_add_succeeds_without_flag_file(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """フラグファイルを作成しなくても、private-notesが存在すれば`mq add`が成功する。"""
        notes = tmp_path / "private-notes"
        notes.mkdir()
        (notes / "inbox").mkdir(parents=True)
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()
        monkeypatch.setattr(subprocess, "run", _make_git_remote_fake(myrepo))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "add", str(myrepo), "dummy message"], home=tmp_path, now=_FIXED_DT)

        assert exc_info.value.code == 0
        assert "1件投入:" in capsys.readouterr().out


class TestPrivateNotesMissing:
    """`AGENT_TOOLKIT_PRIVATE_NOTES`で明示指定したパスが不在の場合にexit 1とディレクトリ不在案内を返すこと。

    conftestの`_atk_private_notes_env`が全テストへ`AGENT_TOOLKIT_PRIVATE_NOTES=tmp_path/private-notes`を
    設定するため、当該ディレクトリを作成しない限り「明示指定パスが不在」の分岐（自動生成の対象外）を検証できる。
    """

    def test_exits_with_directory_missing_guide(self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
        """管理repo rootが存在しない場合はexit 1でディレクトリ不在案内を出力する。"""
        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "add", str(tmp_path / "myrepo"), "dummy message"], home=tmp_path)

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
        notes = _setup_notes(tmp_path)

        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()
        git_calls: list[_GitCall] = []

        def fake_run(cmd: list[str], *_args: object, **kwargs: object) -> subprocess.CompletedProcess[Any]:
            git_calls.append({"cmd": list(cmd), "kwargs": dict(kwargs)})
            if cmd == ["git", "-C", str(myrepo), "rev-parse", "--is-inside-work-tree"]:
                return subprocess.CompletedProcess(cmd, returncode=0, stdout="true\n", stderr="")
            if cmd == ["git", "-C", str(myrepo), "remote", "get-url", "origin"]:
                stdout: Any = (
                    "https://github.com/example/myrepo.git\n"
                    if kwargs.get("text")
                    else b"https://github.com/example/myrepo.git\n"
                )
                return subprocess.CompletedProcess(cmd, returncode=0, stdout=stdout, stderr="" if kwargs.get("text") else b"")
            if cmd == ["git", "-C", str(myrepo), "rev-parse", "--verify", "HEAD^{commit}"]:
                return subprocess.CompletedProcess(cmd, returncode=0, stdout=f"{_FIXED_HEAD_COMMIT}\n", stderr="")
            empty: Any = "" if kwargs.get("text") else b""
            return subprocess.CompletedProcess(cmd, returncode=0, stdout=empty, stderr=empty)

        monkeypatch.setattr(subprocess, "run", fake_run)

        repo_path = str(myrepo)
        message = "テストメッセージ"

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "add", repo_path, message], home=tmp_path, now=_FIXED_DT)

        assert exc_info.value.code == 0

        inbox_dir = notes / "inbox"
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
        assert git_cmds[pull_idx + 1] == ["git", "add", "inbox"]
        assert git_cmds[pull_idx + 2] == ["git", "commit", "-m", "chore: add 1 feedback item"]
        assert git_cmds[pull_idx + 3] == ["git", "push"]
        for call in git_calls:
            if call["cmd"][:2] != ["git", "-C"]:
                assert call["kwargs"].get("cwd") == notes

        captured = capsys.readouterr()
        assert "1件投入:\n" in captured.out
        assert f"  ~/private-notes/inbox/{files[0].name}\n" in captured.out
        assert "inbox: 計1件" in captured.out
        assert "編集する場合:\n" in captured.out
        assert f"  atk mq edit {files[0].name}\n" in captured.out


class TestMqLifecycleScenario:
    """add→list→start-processing→adoptの一連運用フローを検証する。"""

    def test_add_then_list_then_start_processing_then_adopt(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """登録したエントリが一覧表示を経て処理中・採用済みへ遷移する。"""
        notes = _setup_notes(tmp_path)
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()
        git_calls: list[_GitCall] = []

        def fake_run(cmd: list[str], *_args: object, **kwargs: object) -> subprocess.CompletedProcess[Any]:
            git_calls.append({"cmd": list(cmd), "kwargs": dict(kwargs)})
            if cmd == ["git", "-C", str(myrepo), "rev-parse", "--is-inside-work-tree"]:
                return subprocess.CompletedProcess(cmd, returncode=0, stdout="true\n", stderr="")
            if cmd == ["git", "-C", str(myrepo), "remote", "get-url", "origin"]:
                stdout: Any = (
                    "https://github.com/example/myrepo.git\n"
                    if kwargs.get("text")
                    else b"https://github.com/example/myrepo.git\n"
                )
                return subprocess.CompletedProcess(cmd, returncode=0, stdout=stdout, stderr="" if kwargs.get("text") else b"")
            if cmd == ["git", "-C", str(myrepo), "rev-parse", "--verify", "HEAD^{commit}"]:
                return subprocess.CompletedProcess(cmd, returncode=0, stdout=f"{_FIXED_HEAD_COMMIT}\n", stderr="")
            empty: Any = "" if kwargs.get("text") else b""
            return subprocess.CompletedProcess(cmd, returncode=0, stdout=empty, stderr=empty)

        monkeypatch.setattr(subprocess, "run", fake_run)
        filename = f"{_FIXED_TIMESTAMP}-001.md"
        message = "ライフサイクル確認"

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "add", str(myrepo), message], home=tmp_path, now=_FIXED_DT)
        assert exc_info.value.code == 0
        capsys.readouterr()

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "list"], home=tmp_path)
        assert exc_info.value.code == 0
        expected = f"{filename}: github.com/example/myrepo [inbox/normal/ready] {message}"
        assert expected in capsys.readouterr().out

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "start-processing", filename], home=tmp_path)
        assert exc_info.value.code == 0
        assert not (notes / "inbox" / filename).exists()
        assert (notes / "processing" / filename).exists()

        git_calls.clear()
        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "adopt", filename], home=tmp_path)
        assert exc_info.value.code == 0
        assert not (notes / "processing" / filename).exists()
        assert (notes / "adopted" / filename).exists()
        git_cmds = [call["cmd"] for call in git_calls]
        assert any(cmd[:2] == ["git", "add"] for cmd in git_cmds)
        assert any(cmd[:2] == ["git", "commit"] for cmd in git_cmds)
        assert ["git", "push"] in git_cmds


class TestAddCompletionShowsProcessingCount:
    """addサブコマンド: 完了表示の「inbox: 計X件」にprocessing件数を併記する（フィードバック20260724-075120-001反映）。"""

    def test_processing_count_shown_alongside_inbox_count(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """processing配下に既存ファイルがある状態で`mq add`すると、その件数が併記される。"""
        notes = _setup_notes(tmp_path)
        processing_dir = notes / "processing"
        processing_dir.mkdir(parents=True)
        (processing_dir / "existing-001.md").write_text(
            "---\ntype: feedback\ntarget_repo: github.com/example/foo\n---\n\n既存処理中\n", encoding="utf-8"
        )
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()
        monkeypatch.setattr(subprocess, "run", _make_git_remote_fake(myrepo))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "add", str(myrepo), "テストメッセージ"], home=tmp_path, now=_FIXED_DT)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "inbox: 計1件（processing: 1件）" in captured.out

    def test_processing_count_is_zero_when_none_processing(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """processing配下が空の状態でも0件と明示される。"""
        _setup_notes(tmp_path)
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()
        monkeypatch.setattr(subprocess, "run", _make_git_remote_fake(myrepo))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "add", str(myrepo), "テストメッセージ"], home=tmp_path, now=_FIXED_DT)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "inbox: 計1件（processing: 0件）" in captured.out


class TestAddCompletionShowsTargetRepoBreakdown:
    """addサブコマンド: 完了表示へ全体件数に加えて対象リポジトリ分の内訳を併記する。"""

    def test_breakdown_excludes_other_repo_processing_entries(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """別リポジトリ宛のprocessingエントリは対象リポジトリ分の内訳へ数えない。"""
        notes = _setup_notes(tmp_path)
        processing_dir = notes / "processing"
        processing_dir.mkdir(parents=True)
        (processing_dir / "existing-001.md").write_text(
            "---\ntype: feedback\ntarget_repo: github.com/example/foo\n---\n\n別リポジトリの処理中\n", encoding="utf-8"
        )
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()
        monkeypatch.setattr(subprocess, "run", _make_git_remote_fake(myrepo))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "add", str(myrepo), "テストメッセージ"], home=tmp_path, now=_FIXED_DT)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "inbox: 計1件（processing: 1件）" in captured.out
        assert "  うちgithub.com/example/myrepo: 1件（processing: 0件）" in captured.out

    def test_breakdown_excludes_other_repo_inbox_entries(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """別リポジトリ宛のinboxエントリは対象リポジトリ分の内訳へ数えない。"""
        notes = _setup_notes(tmp_path)
        inbox_dir = notes / "inbox"
        inbox_dir.mkdir(parents=True, exist_ok=True)
        (inbox_dir / "existing-001.md").write_text(
            "---\ntype: feedback\ntarget_repo: github.com/example/foo\n---\n\n別リポジトリの未処理\n", encoding="utf-8"
        )
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()
        monkeypatch.setattr(subprocess, "run", _make_git_remote_fake(myrepo))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "add", str(myrepo), "テストメッセージ"], home=tmp_path, now=_FIXED_DT)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "inbox: 計2件（processing: 0件）" in captured.out
        assert "  うちgithub.com/example/myrepo: 1件（processing: 0件）" in captured.out


class TestAddMultipleMessages:
    """addサブコマンド: 2件以上のメッセージで連番と件数コミットメッセージを検証する。"""

    def test_multiple_messages_generate_files_with_sequence(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """2件のメッセージで連番001・002の付与とコミットメッセージを検証する。"""
        notes = _setup_notes(tmp_path)

        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()
        git_calls: list[_GitCall] = []

        def fake_run(cmd: list[str], *_args: object, **kwargs: object) -> subprocess.CompletedProcess[Any]:
            git_calls.append({"cmd": list(cmd), "kwargs": dict(kwargs)})
            if cmd == ["git", "-C", str(myrepo), "rev-parse", "--is-inside-work-tree"]:
                return subprocess.CompletedProcess(cmd, returncode=0, stdout="true\n", stderr="")
            if cmd == ["git", "-C", str(myrepo), "remote", "get-url", "origin"]:
                stdout: Any = (
                    "https://github.com/example/myrepo.git\n"
                    if kwargs.get("text")
                    else b"https://github.com/example/myrepo.git\n"
                )
                return subprocess.CompletedProcess(cmd, returncode=0, stdout=stdout, stderr="" if kwargs.get("text") else b"")
            if cmd == ["git", "-C", str(myrepo), "rev-parse", "--verify", "HEAD^{commit}"]:
                return subprocess.CompletedProcess(cmd, returncode=0, stdout=f"{_FIXED_HEAD_COMMIT}\n", stderr="")
            empty: Any = "" if kwargs.get("text") else b""
            return subprocess.CompletedProcess(cmd, returncode=0, stdout=empty, stderr=empty)

        monkeypatch.setattr(subprocess, "run", fake_run)

        repo_path = str(myrepo)

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "add", repo_path, "メッセージ1", "メッセージ2"], home=tmp_path, now=_FIXED_DT)

        assert exc_info.value.code == 0

        inbox_dir = notes / "inbox"
        files = sorted(inbox_dir.iterdir())
        assert len(files) == 2
        assert files[0].name == f"{_FIXED_TIMESTAMP}-001.md"
        assert files[1].name == f"{_FIXED_TIMESTAMP}-002.md"

        commit_cmd = [c["cmd"] for c in git_calls if "commit" in c["cmd"]][0]
        assert "chore: add 2 feedback items" in commit_cmd

        captured = capsys.readouterr()
        assert "2件投入:\n" in captured.out
        assert f"  ~/private-notes/inbox/{files[0].name}\n" in captured.out
        assert f"  ~/private-notes/inbox/{files[1].name}\n" in captured.out
        assert "inbox: 計2件" in captured.out
        assert "編集する場合:\n" in captured.out
        assert f"  atk mq edit {files[0].name}\n" in captured.out
        assert f"  atk mq edit {files[1].name}\n" in captured.out


class TestAddRepoPathExpansion:
    """addサブコマンド: ~プレフィックスのrepo_pathがリモートURLへ正規化されること。"""

    def test_tilde_repo_path_is_expanded(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """~展開後にgit remote get-urlでリモートURLが取得され、target_repoへ書き込まれる。"""
        notes = _setup_notes(tmp_path)
        monkeypatch.setenv("HOME", str(tmp_path))

        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()

        monkeypatch.setattr(subprocess, "run", _make_git_remote_fake(myrepo))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "add", "~/myrepo", "テストメッセージ"], home=tmp_path, now=_FIXED_DT)

        assert exc_info.value.code == 0

        inbox = notes / "inbox"
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
        assert body == "\n本文"

    def test_leading_frontmatter_source_priority(self) -> None:
        """先頭frontmatterに`source`が含まれる場合はパース結果に含まれる。"""
        message = "---\ntarget_repo: github.com/x/y\nsource: session-review\n---\n\n本文"
        fm, body = _add._parse_leading_frontmatter(message)  # pylint: disable=protected-access  # noqa: SLF001
        assert fm == {"target_repo": "github.com/x/y", "source": "session-review"}
        assert body == "\n本文"

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
        notes = _setup_notes(tmp_path)
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()

        monkeypatch.setattr(subprocess, "run", _make_git_remote_fake(myrepo))

        message = "---\ntarget_repo: github.com/other/repo\nsource: session-review\n---\n\nテスト本文"

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "add", str(myrepo), message, "--source", "cli-source"], home=tmp_path, now=_FIXED_DT)
        assert exc_info.value.code == 0

        inbox = notes / "inbox"
        files = list(inbox.iterdir())
        assert len(files) == 1
        content = files[0].read_text(encoding="utf-8")
        assert "target_repo: github.com/other/repo" in content
        assert "source: session-review" in content
        body = content.split("---\n\n", 1)[1]
        assert body == "テスト本文"

    def test_multiple_messages_mixed_frontmatter(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """複数メッセージ混在時（一部のみfrontmatter付き）に各メッセージが独立判定される。"""
        notes = _setup_notes(tmp_path)
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()

        monkeypatch.setattr(subprocess, "run", _make_git_remote_fake(myrepo))

        msg_with_fm = "---\ntarget_repo: github.com/override/repo\n---\n\nfm付き本文"
        msg_plain = "frontmatter無し本文"

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "add", str(myrepo), msg_with_fm, msg_plain], home=tmp_path, now=_FIXED_DT)
        assert exc_info.value.code == 0

        inbox = notes / "inbox"
        files = sorted(inbox.iterdir())
        assert len(files) == 2
        content_first = files[0].read_text(encoding="utf-8")
        content_second = files[1].read_text(encoding="utf-8")
        assert "target_repo: github.com/override/repo" in content_first
        assert content_first.split("---\n\n", 1)[1] == "fm付き本文"
        assert "target_repo: github.com/example/myrepo" in content_second
        assert content_second.split("---\n\n", 1)[1] == msg_plain + "\n"

    def test_frontmatter_target_repo_only_falls_back_to_cli_source(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """frontmatterに`target_repo`のみで`source`未指定の場合、CLIオプション値を採用する。"""
        notes = _setup_notes(tmp_path)
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()

        monkeypatch.setattr(subprocess, "run", _make_git_remote_fake(myrepo))

        message = "---\ntarget_repo: github.com/other/repo\n---\n\n本文"

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "add", str(myrepo), message, "--source", "cli-source"], home=tmp_path, now=_FIXED_DT)
        assert exc_info.value.code == 0

        inbox = notes / "inbox"
        files = list(inbox.iterdir())
        assert len(files) == 1
        content = files[0].read_text(encoding="utf-8")
        assert "target_repo: github.com/other/repo" in content
        assert "source: cli-source" in content


def _write_tbd_file(
    notes: pathlib.Path,
    filename: str,
    target_repo: str = "github.com/example/foo",
    question: str = "テスト質問",
    answer: str = "",
    source: str | None = None,
) -> pathlib.Path:
    """inbox配下に1ファイルを書き込み、絶対パスを返す。`source`指定時はfrontmatterへ追記する。"""
    tbd_dir = notes / "inbox"
    tbd_dir.mkdir(parents=True, exist_ok=True)
    path = tbd_dir / filename
    source_line = f"source: {source}\n" if source is not None else ""
    path.write_text(
        f"---\ncreated: {_FIXED_ISO}\ntarget_repo: {target_repo}\ntype: tbd\n"
        f"question_type: free-form\n{source_line}---\n\n"
        f"## 質問\n\n{question}\n\n## 回答\n\n{answer}",
        encoding="utf-8",
    )
    return path

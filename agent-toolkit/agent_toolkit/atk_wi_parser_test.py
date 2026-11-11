"""atk wi parserの契約を検証する。"""

from __future__ import annotations

import datetime
import pathlib
import subprocess
from collections.abc import Callable

import pytest

from agent_toolkit import atk
from agent_toolkit._atk.wi import entries as _wi_entries
from agent_toolkit._testing import git_repository, wi_entry_files
from agent_toolkit._testing.atk_support import (
    _ATK_PATH,
    _PROJECT_ROOT,
    _REMOVED_RUN_SCRIPTS,
    _isolate_agent_and_managed_temp_environment,
    _isolated_cli_environ,
    _make_subprocess_fake,
    _setup_notes,
    _write_awi_file,
    _write_uwi_file,
)

__all__ = ["_isolate_agent_and_managed_temp_environment"]


def test_cli_local_path_filter_counts_legacy_and_current_uwis(
    tmp_path: pathlib.Path,
    host_environ: Callable[[], dict[str, str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """実CLIはローカルパス指定時に旧パス形とURL形のUWIをともに数える。"""
    notes = _setup_notes(tmp_path)
    local_repo = tmp_path / "repo"
    git_repository.init_repository(local_repo, origin="git@github.com:example/repo.git")
    _write_uwi_file(notes, "legacy.md", target_repo=str(local_repo), question="旧形式")
    _write_uwi_file(notes, "current.md", target_repo="github.com/example/repo", question="現行形式")
    _write_uwi_file(notes, "other.md", target_repo="github.com/example/other", question="対象外")
    monkeypatch.setenv("UV_FROZEN", "1")
    monkeypatch.setenv("VIRTUAL_ENV", str(tmp_path / "parent-venv"))
    env = _isolated_cli_environ(host_environ, tmp_path)
    env["AGENT_TOOLKIT_PRIVATE_NOTES"] = str(notes)
    # 子projectのlock指定と環境を使い、親のuv設定による警告をCLI出力へ混ぜない。
    env.pop("UV_FROZEN", None)
    env.pop("VIRTUAL_ENV", None)

    result = subprocess.run(  # noqa: S603
        [
            "uv",
            "run",
            "--project",
            str(_PROJECT_ROOT),
            "--locked",
            "--no-default-groups",
            str(_ATK_PATH),
            "wi",
            "list",
            "--count",
            f"--target-repo={local_repo}",
            "--skip-pull",
        ],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )

    assert result.returncode == 0
    assert result.stdout == "2\n"
    assert not result.stderr


class TestMutationTargetRepoParserOption:
    """mutation系サブコマンドの`--target-repo`受理をargparseレベルで検証する。"""

    @pytest.mark.parametrize(
        ("top_command", "subcommand", "argv_tail"),
        [
            ("wi", "adopt", ["20260714-000001-001.md"]),
            ("wi", "reject", ["20260714-000001-001.md"]),
            ("wi", "rm", ["20260714-000001-001.md"]),
            ("wi", "edit", ["20260714-000001-001.md"]),
            ("wi", "start-processing", ["20260714-000001-001.md"]),
            ("wi", "return-to-inbox", ["20260714-000001-001.md"]),
        ],
    )
    def test_accepts_target_repo(self, top_command: str, subcommand: str, argv_tail: list[str]) -> None:
        """6種のmutation系サブコマンドすべてが`--target-repo`を受理する。"""
        parser = atk._build_parser()  # pylint: disable=protected-access  # noqa: SLF001
        args = parser.parse_args([top_command, subcommand, "--target-repo", "github.com/foo/bar", *argv_tail])
        expected = "github.com/foo/bar" if subcommand == "edit" else ["github.com/foo/bar"]
        assert args.target_repo == expected

    def test_edit_rejects_message(self) -> None:
        """`edit FILENAME MESSAGE`の旧受理形式を拒否する。"""
        parser = atk._build_parser()  # pylint: disable=protected-access  # noqa: SLF001
        with pytest.raises(SystemExit) as exc_info:
            parser.parse_args(["wi", "edit", "20260714-000001-001.md", "更新本文"])
        assert exc_info.value.code == 2

    def test_edit_accepts_append_option(self) -> None:
        """`edit --append FILENAME --body-file PATH`を追記モードとして解析する。"""
        parser = atk._build_parser()  # pylint: disable=protected-access  # noqa: SLF001
        args = parser.parse_args(["wi", "edit", "--append", "20260714-000001-001.md", "--body-file", "body.md"])
        assert args.append is True
        assert args.filename == "20260714-000001-001.md"
        assert args.body_file == "body.md"

    def test_edit_accepts_body_file(self) -> None:
        """`edit --body-file`を単一の本文ファイルとして解析する。"""
        parser = atk._build_parser()  # pylint: disable=protected-access  # noqa: SLF001
        args = parser.parse_args(["wi", "edit", "entry.md", "--body-file", "body.md"])
        assert args.body_file == "body.md"

    @pytest.mark.parametrize("value", ["2", "3.5", "three"])
    def test_return_to_inbox_rejects_invalid_cooldown_days(self, value: str) -> None:
        """再処理抑制日数は3以上の整数だけを受理する。"""
        parser = atk._build_parser()  # pylint: disable=protected-access  # noqa: SLF001
        with pytest.raises(SystemExit):
            parser.parse_args(["wi", "return-to-inbox", "entry.md", f"--cooldown-days={value}"])

    def test_return_to_inbox_accepts_minimum_cooldown_days(self) -> None:
        """再処理抑制日数の下限3を受理する。"""
        parser = atk._build_parser()  # pylint: disable=protected-access  # noqa: SLF001
        args = parser.parse_args(["wi", "return-to-inbox", "entry.md", "--cooldown-days=3"])
        assert args.cooldown_days == 3

    def test_return_to_inbox_uses_main_injected_time(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """mainへ注入した基準時刻から再処理抑制期限を保存する。"""
        notes = _setup_notes(tmp_path)
        entry = wi_entry_files.write_awi(notes, "entry.md", state="processing", target_repo="github.com/example/foo")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))
        now = datetime.datetime(2026, 8, 12, 3, 4, 5, tzinfo=datetime.UTC)

        with pytest.raises(SystemExit) as exc_info:
            atk.main(
                ["wi", "return-to-inbox", "entry.md", "--cooldown-days=3"],
                home=tmp_path,
                now=now,
            )

        assert exc_info.value.code == 0
        content = entry.read_text(encoding="utf-8")
        assert _wi_entries.entry_state_of(entry, content) == "inbox"
        assert "cooldown_until: '2026-08-15T03:04:05+00:00'" in content

    def test_edit_without_message_remains_interactive(self) -> None:
        """`edit FILENAME`では本文ファイルを未指定として扱う。"""
        parser = atk._build_parser()  # pylint: disable=protected-access  # noqa: SLF001
        args = parser.parse_args(["wi", "edit", "20260714-000001-001.md"])
        assert args.filename == "20260714-000001-001.md"
        assert args.body_file is None

    def test_commit_has_no_target_repo_option(self) -> None:
        """`commit`は引数を取らないシグネチャのため`--target-repo`を受理しない。"""
        parser = atk._build_parser()  # pylint: disable=protected-access  # noqa: SLF001
        with pytest.raises(SystemExit) as exc_info:
            parser.parse_args(["wi", "commit", "--target-repo", "github.com/foo/bar"])
        assert exc_info.value.code == 2


def test_repeatable_wi_filters_preserve_all_values() -> None:
    """一覧系フィルターは同じオプションの全指定値を構文解析結果へ保持する。"""
    parser = atk._build_parser()  # pylint: disable=protected-access  # noqa: SLF001
    args = parser.parse_args(
        [
            "wi",
            "list",
            "--target-repo=github.com/example/foo",
            "--target-repo=github.com/example/bar",
            "--type=awi",
            "--type=uwi",
            "--status=inbox",
            "--status=hold",
            "--answered=yes",
            "--answered=no",
            "--source=human",
            "--source=!agent",
        ]
    )

    assert args.target_repo == ["github.com/example/foo", "github.com/example/bar"]
    assert args.type == ["awi", "uwi"]
    assert args.status == ["inbox", "hold"]
    assert args.answered == ["yes", "no"]
    assert args.source == ["human", "!agent"]


@pytest.mark.parametrize("subcommand", ("adopt", "reject", "rm"))
def test_note_and_note_file_are_mutually_exclusive(subcommand: str) -> None:
    """3つの状態変更コマンドは文字列メモとファイルメモの併用を構文解析時に拒否する。"""
    parser = atk._build_parser()  # pylint: disable=protected-access  # noqa: SLF001

    with pytest.raises(SystemExit) as exc_info:
        parser.parse_args(["wi", subcommand, "entry.md", "--note=本文", "--note-file=/tmp/note.txt"])

    assert exc_info.value.code == 2


def test_note_file_preserves_utf8_text(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """ファイルメモは改行とシェル制御文字を変更せず既存の採用処理へ渡す。"""
    notes = _setup_notes(tmp_path)
    entry = _write_awi_file(notes, "entry.md")
    note_file = tmp_path / "note.txt"
    note = '1行目\n"引用"と`backtick`と$記号\n'
    note_file.write_text(note, encoding="utf-8")
    monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

    with pytest.raises(SystemExit) as exc_info:
        atk.main(["wi", "adopt", "entry.md", "--note-file", str(note_file)], home=tmp_path)

    assert exc_info.value.code == 0
    assert f"- メモ: {note}" in entry.read_text(encoding="utf-8")
    assert _wi_entries.entry_state_of(entry) == "adopted"


@pytest.mark.parametrize("invalid_bytes", (None, b"\xff"))
def test_note_file_read_error_precedes_environment_changes(
    tmp_path: pathlib.Path,
    invalid_bytes: bytes | None,
) -> None:
    """存在しないまたはUTF-8でないメモはprivate-notesの準備前に拒否する。"""
    note_file = tmp_path / "note.txt"
    if invalid_bytes is not None:
        note_file.write_bytes(invalid_bytes)

    with pytest.raises(SystemExit) as exc_info:
        atk.main(["wi", "reject", "entry.md", "--note-file", str(note_file)], home=tmp_path)

    assert exc_info.value.code == 2
    assert not (tmp_path / "private-notes").exists()


@pytest.mark.parametrize("subcommand", ("adopt", "reject", "rm"))
def test_note_file_rejects_relative_path_before_environment_changes(
    tmp_path: pathlib.Path,
    subcommand: str,
) -> None:
    """相対メモパスはprivate-notesの準備前に3コマンドで拒否する。"""
    with pytest.raises(SystemExit) as exc_info:
        atk.main(["wi", subcommand, "entry.md", "--note-file=note.txt"], home=tmp_path)

    assert exc_info.value.code == 2
    assert not (tmp_path / "private-notes").exists()


@pytest.mark.parametrize(
    "argv",
    [
        ["wi", "convert-to-plan", "awi.md", "--plan-file", "/tmp/plan.md"],
        ["plans", "progress", "2026/09/09-example-1a2b.md"],
        ["plans", "migrate"],
    ],
)
def test_removed_plan_mutation_commands_are_rejected(argv: list[str]) -> None:
    """廃止された計画変換・更新コマンドを受理しない。"""
    parser = atk._build_parser()  # pylint: disable=protected-access  # noqa: SLF001
    with pytest.raises(SystemExit) as exc_info:
        parser.parse_args(argv)
    assert exc_info.value.code == 2


@pytest.mark.parametrize(
    "argv",
    [
        ["managed-temp", "list"],
        ["managed-temp", "create", "--prefix", "example"],
        ["managed-temp", "cleanup", "--path", "/absolute/example"],
        ["read-file", "--", "/absolute/example"],
        ["run-command", "--", "true"],
        ["watch", "--file", "/absolute/example"],
        ["plans", "rewrite-references"],
        ["worktree-stash", "protect", "refs/worktree/example"],
        ["wait-schedule", "--request-bucket", "main"],
        *(["run-script", name, "--"] for name in _REMOVED_RUN_SCRIPTS),
    ],
)
def test_removed_public_paths_are_rejected(argv: list[str]) -> None:
    """標準手段へ置き換えて削除した公開経路を、引数の誤りとして受理しない。"""
    parser = atk._build_parser()  # pylint: disable=protected-access  # noqa: SLF001
    with pytest.raises(SystemExit) as exc_info:
        parser.parse_args(argv)
    assert exc_info.value.code == 2


def test_retained_run_scripts_are_exactly_the_kept_registrations() -> None:
    """残す22登録だけを公開し、撤去した9登録を含めない。"""
    from agent_toolkit._atk import run_script  # noqa: PLC0415  # pylint: disable=import-outside-toplevel

    assert sorted(run_script.SCRIPT_PATHS) == sorted(
        (
            "wait-ci",
            "cost-metrics",
            "review-adoption",
            "delegation-prompt",
            "history-compare",
            "commit-with-timeout",
            "agent-doc-changes",
            "plan-create",
            "plan-check",
            "plan-progress",
            "plan-commits",
            "plan-rewrite",
            "pick-wi-check",
            "termination-evidence",
            "multi-turn-trials",
            "record-stall-detection",
            "wait-compaction-context",
            "session-review-evidence",
            "session-review-prepare",
            "session-review-state",
            "writing-check-dash",
            "wi-quote-check",
        )
    )
    assert not set(_REMOVED_RUN_SCRIPTS) & set(run_script.SCRIPT_PATHS)


def test_removed_mq_alias_is_rejected() -> None:
    """廃止済みの`mq`は公開CLIの未知コマンドとして終了する。"""
    with pytest.raises(SystemExit) as raised:
        atk.main(["mq", "--help"])
    assert raised.value.code == 2


def test_set_dependencies_parser_accepts_repeated_dependencies() -> None:
    """`set-dependencies`が既存AWIと複数の依存先を受理する。"""
    parser = atk._build_parser()  # pylint: disable=protected-access  # noqa: SLF001
    args = parser.parse_args(["wi", "set-dependencies", "awi.md", "--depends-on", "first", "--depends-on", "second.md"])
    assert args.filename == "awi.md"
    assert args.depends_on == ["first", "second.md"]


class TestUwiAddSourceOptionParser:
    """UWI投入時の`--source`受理をargparseレベルで検証する。"""

    def test_accepts_source(self) -> None:
        """`mq add --type=uwi`が`--source`を受理しargs.sourceへ格納される。"""
        parser = atk._build_parser()  # pylint: disable=protected-access  # noqa: SLF001
        args = parser.parse_args(["wi", "add", "--type=uwi", "--source", "session-hold"])
        assert args.source == "session-hold"


class TestAddTargetRepoOptionParser:
    """`mq add`の`--target-repo`受理をargparseレベルで検証する。"""

    @pytest.mark.parametrize("type_option", [[], ["--type=uwi"]])
    def test_add_accepts_target_repo(self, type_option: list[str]) -> None:
        """`mq add`が種別にかかわらず`--target-repo`を受理する。"""
        parser = atk._build_parser()  # pylint: disable=protected-access  # noqa: SLF001
        args = parser.parse_args(["wi", "add", *type_option, "--target-repo", "github.com/foo/bar"])
        assert args.target_repo == "github.com/foo/bar"


class TestSpaceSeparatedOptionWithoutWarning:
    """受理に成功した空白区切りの値付きオプションへ形式の警告を出力しない。

    空白区切りの値もargparseが正しく受理するため、警告は呼び出しの結果と後続の操作を変えない。
    推奨形式の案内はヘルプだけが担う。
    """

    @pytest.mark.parametrize("subcommand", ("adopt", "reject"))
    def test_successful_space_separated_call_emits_no_format_warning(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
        subcommand: str,
    ) -> None:
        notes = _setup_notes(tmp_path)
        _write_awi_file(notes, "fb-001.md")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", subcommand, "fb-001.md", "--note", "memo"], home=tmp_path)

        assert exc_info.value.code == 0
        assert "VALUE形式" not in capsys.readouterr().err

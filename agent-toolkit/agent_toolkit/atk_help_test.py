"""`atk`のコマンド木全体のヘルプ契約を検証する。"""

from __future__ import annotations

import argparse
import pathlib
from collections.abc import Iterator

import pytest

from agent_toolkit import atk
from agent_toolkit._atk import help_text as _atk_help
from agent_toolkit._atk import outcome as _outcome
from agent_toolkit._common import wait_schedule


def test_info_reports_current_environment_without_creating_config(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """診断値の出所と未作成の設定を表示し、状態を変更しない。"""
    monkeypatch.chdir(tmp_path)
    config = tmp_path / "config.json"
    monkeypatch.setattr(atk._config_cmd, "_config_file_path", lambda: config)  # pylint: disable=protected-access

    atk.main(["info"])

    output = capsys.readouterr().out
    assert f"作業ディレクトリ: {tmp_path}" in output
    assert "plugin version (plugin.json): " in output
    assert "設定ファイル候補:" not in output
    assert f"設定ファイル: {config}（未作成）" in output
    assert not config.exists()


_DESCRIPTION_MARKERS = ("目的:", "利用場面:", "対象と出力:", "前提:", "復元・後始末:")


def _walk_commands() -> Iterator[tuple[str, argparse.ArgumentParser, str | None]]:
    root = atk._build_parser()  # pylint: disable=protected-access
    yield "atk", root, None
    pending = [("atk", root)]
    while pending:
        parent_name, parent = pending.pop(0)
        for action in parent._actions:  # pylint: disable=protected-access
            if not isinstance(action, argparse._SubParsersAction):  # pylint: disable=protected-access
                continue
            summaries = {
                choice.dest: choice.help
                for choice in action._choices_actions  # pylint: disable=protected-access
            }
            for name, child in action.choices.items():
                command = f"{parent_name} {name}"
                yield command, child, summaries[name]
                pending.append((command, child))


def test_every_command_has_summary_and_description() -> None:
    commands = list(_walk_commands())

    assert len(commands) == len(_atk_help.HELP) + 1
    for command, parser, summary in commands:
        assert parser.description, command
        if command != "atk":
            assert summary, command


def test_every_command_describes_purpose_scene_effect_precondition_and_recovery() -> None:
    for command, parser, _summary in _walk_commands():
        assert parser.description is not None, command
        for marker in _DESCRIPTION_MARKERS:
            assert marker in parser.description, (command, marker)
        assert parser.epilog is not None, command
        assert "実行例:" in parser.epilog, command
        assert "```" not in parser.epilog, command
        examples = parser.epilog.split("実行例:\n\n", maxsplit=1)[1].split("\n\n", maxsplit=1)[0]
        assert all(line.startswith("  ") for line in examples.splitlines()), command


@pytest.mark.parametrize(
    "argv",
    [[], ["wi"], ["plans"], ["managed-temp"], ["review-table"], ["review-audit"]],
)
def test_command_without_subcommand_prints_help(
    argv: list[str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    atk.main(argv)

    captured = capsys.readouterr()
    assert "位置引数:" in captured.out
    assert captured.err == ""


def test_every_argument_has_help() -> None:
    for command, parser, _summary in _walk_commands():
        for action in parser._actions:  # pylint: disable=protected-access
            if isinstance(action, argparse._SubParsersAction):  # pylint: disable=protected-access
                continue
            assert action.help is not None, (command, action.dest)


def test_help_sections_and_builtin_help_are_japanese() -> None:
    for command, parser, _summary in _walk_commands():
        help_text = parser.format_help()
        assert "使い方: " in help_text, command
        assert "オプション:" in help_text, command
        assert "このヘルプを表示して終了する" in help_text, command
        assert "usage: " not in help_text, command
        assert "options:" not in help_text, command
        assert "positional arguments:" not in help_text, command
        if any(
            isinstance(action, argparse._SubParsersAction)  # pylint: disable=protected-access
            or (not action.option_strings and action.dest != argparse.SUPPRESS)
            for action in parser._actions  # pylint: disable=protected-access
        ):
            assert "位置引数:" in help_text, command
        if any(isinstance(action, argparse._SubParsersAction) for action in parser._actions):  # pylint: disable=protected-access
            assert "実行するサブコマンド" in help_text, command


def _required_arguments(parser: argparse.ArgumentParser) -> tuple[list[str], argparse.ArgumentParser]:
    arguments: list[str] = []
    for action in parser._actions:  # pylint: disable=protected-access
        if isinstance(action, argparse._SubParsersAction):  # pylint: disable=protected-access
            if action.required:
                name, child = next(iter(action.choices.items()))
                child_arguments, target = _required_arguments(child)
                arguments.extend((name, *child_arguments))
                return arguments, target
            continue
        value = str(next(iter(action.choices))) if action.choices else "1" if action.type in (int, float) else "value"
        if action.option_strings:
            if action.required:
                option = next(option for option in action.option_strings if option.startswith("--"))
                arguments.extend((option, value))
            continue
        if action.nargs not in ("?", "*"):
            arguments.append(value)
    for group in parser._mutually_exclusive_groups:  # pylint: disable=protected-access
        if not group.required:
            continue
        action = group._group_actions[0]  # pylint: disable=protected-access
        value = str(next(iter(action.choices))) if action.choices else "1" if action.type in (int, float) else "value"
        option = next(option for option in action.option_strings if option.startswith("--"))
        arguments.extend((option, value))
    return arguments, parser


def test_every_subcommand_rejects_unsupported_argument_with_accepted_options(
    capsys: pytest.CaptureFixture[str],
) -> None:
    for command, parser, _summary in _walk_commands():
        if command == "atk":
            continue
        arguments, target = _required_arguments(parser)
        if any(
            action.nargs == argparse.REMAINDER
            for action in target._actions  # pylint: disable=protected-access
        ):
            continue
        with pytest.raises(SystemExit) as exc_info:
            parser.parse_args([*arguments, "--unsupported"])

        assert exc_info.value.code == 2, command
        error = capsys.readouterr().err
        assert error.startswith(f"使い方: {target.prog}"), command
        options = sorted(
            option
            for option in target._option_string_actions  # pylint: disable=protected-access
            if option.startswith("--") and option != "--help"
        )
        accepted = "・".join(options) if options else "なし"
        assert f"解釈できない引数: --unsupported。{target.prog}が受理するオプションは{accepted}" in error, command


def test_wrapped_help_keeps_identifiers_intact(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COLUMNS", "60")
    commands = {command: parser for command, parser, _summary in _walk_commands()}

    mq_add_help = commands["atk wi add"].format_help()
    assert "--question-type" in mq_add_help
    assert "--target-repo" in mq_add_help
    assert "--dry-run" in mq_add_help
    worktree_stash_help = commands["atk worktree-stash"].format_help()
    assert "refs/worktree/<ラベル>" in worktree_stash_help


@pytest.mark.parametrize(
    "environment",
    [
        {"CLAUDECODE": "1", "CLAUDE_CODE_PROMPT_CACHE_TTL": "1h"},
        {"CLAUDECODE": "1", "CLAUDE_CODE_PROMPT_CACHE_TTL": "5m"},
        {"CLAUDE_CODE_PROMPT_CACHE_TTL": "1h"},
        {"CLAUDECODE": "1", "CLAUDE_CODE_PROMPT_CACHE_TTL": "1h", "AGENT_TOOLKIT_DELEGATED_SESSION": "1"},
    ],
    ids=["ttl-1h", "ttl-5m", "unknown-host", "delegated-session"],
)
def test_agents_wait_help_states_own_limit_and_standalone_invocation(
    monkeypatch: pytest.MonkeyPatch, environment: dict[str, str]
) -> None:
    """`atk agents wait`の公開説明が、各条件で実装が導く待機上限の秒数を示す。

    説明の上限が実装と異なると、待機する主体が上限の無い待機と判断して外側の`timeout`とパイプで包み、
    自前の上限より先に待機を打ち切るうえ、続行の判定に使う終了コードを覆い隠す。
    """
    for name in (
        "CLAUDECODE",
        "CLAUDE_CODE_PROMPT_CACHE_TTL",
        "FORCE_PROMPT_CACHING_5M",
        "AGENT_TOOLKIT_DELEGATED_SESSION",
        "AGENT_TOOLKIT_OWNER_SESSION",
    ):
        monkeypatch.delenv(name, raising=False)
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    limit = wait_schedule.get_wait_timeout("main")
    commands = {name: parser for name, parser, _summary in _walk_commands()}
    description = commands["atk agents wait"].description

    assert description is not None
    assert limit.is_integer()
    assert f"{int(limit)}秒" in description


@pytest.mark.parametrize(
    ("argv", "saved"),
    [
        (["wi", "show", "fb-001.md"], False),
        (["wi", "show", "fb-001.md", "fb-002.md"], True),
        (["wi", "show", "--all"], True),
        (["run-script", "session-review-evidence", "--", "t.jsonl", "--detail", "1"], False),
        (["run-script", "session-review-evidence", "--", "t.jsonl", "--user-events"], True),
        (["agents", "wait"], True),
    ],
)
def test_output_saving_help_matches_cli_condition(argv: list[str], saved: bool) -> None:
    """呼び出しごとに、量によらず標準出力を保存するかの判定が呼び出しの区分と一致する。

    判定が区分と異なると、呼び出し元は短い単発照会でも保存先を探すか、ファイルとして渡す結果を直接表示と誤認する。
    """
    args = atk._build_parser().parse_args(argv)  # pylint: disable=protected-access  # noqa: SLF001
    assert atk._passes_output_as_file(args) is saved  # pylint: disable=protected-access  # noqa: SLF001


def _leaf_commands() -> set[str]:
    """それ自体を実行できるコマンドを返す。

    サブコマンドを持つ場合も、そのサブコマンドが必須でなければそのコマンド自体を実行できるため、
    結果行の区分を要するコマンドとして数える。
    """
    leaves: set[str] = set()
    for command, parser, _summary in _walk_commands():
        subparsers = [
            action
            for action in parser._actions  # pylint: disable=protected-access
            if isinstance(action, argparse._SubParsersAction)  # pylint: disable=protected-access
        ]
        # サブコマンドを持つ場合でも、省略時に自身を実行するコマンドは結果行の区分を要する。
        # サブコマンドが必須であるか、省略時に一覧を表示して終わるコマンドは自身を実行しない。
        shows_help_when_missing = parser.get_default("_help_parser") is not None
        requires_subcommand = any(action.required for action in subparsers)
        if subparsers and (requires_subcommand or shows_help_when_missing):
            continue
        leaves.add(command)
    return leaves


def test_every_leaf_command_belongs_to_one_result_kind() -> None:
    """全リーフサブコマンドが結果行の区分のいずれか1つへ属する。

    区分の対応が無いリーフを追加すると、そのコマンドの成否を結果行の先頭の語で確定できなくなる。
    """
    classified = (
        _outcome.STATE_CHANGE_COMMANDS
        | _outcome.VALUE_OUTPUT_COMMANDS
        | _outcome.READ_ONLY_COMMANDS
        | _outcome.OUT_OF_SCOPE_COMMANDS
    )
    leaves = _leaf_commands()

    assert leaves - classified == set(), "区分の対応が無いリーフサブコマンドがある"
    assert classified - leaves == set(), "実在しないコマンドを区分表が持つ"


def test_result_kinds_do_not_overlap() -> None:
    """同じリーフサブコマンドが複数の区分へ属さない。"""
    groups = (
        _outcome.STATE_CHANGE_COMMANDS,
        _outcome.VALUE_OUTPUT_COMMANDS,
        _outcome.READ_ONLY_COMMANDS,
        _outcome.OUT_OF_SCOPE_COMMANDS,
    )
    total = sum(len(group) for group in groups)

    assert len(frozenset().union(*groups)) == total


def test_bulk_transition_commands_accept_the_same_filter_options() -> None:
    """一括操作を受理する状態遷移コマンドが`rm`と同じフィルター系引数を持つ。"""
    commands = {name: parser for name, parser, _summary in _walk_commands()}
    # `--state`は`return-to-inbox`が差し戻し元の指定に使う綴りと重なるため、このテストの期待集合から除く。
    # フィルターの綴りの別名は`listing_test.py`が検証する。
    expected = {"--all", "--type", "--status", "--answered", "--source", "--yes", "--skip-pull", "--target-repo"}

    for command in (*_atk_help.BULK_TRANSITION_COMMANDS, "atk wi rm"):
        option_strings = {
            option
            for action in commands[command]._actions  # pylint: disable=protected-access
            for option in action.option_strings
        }
        assert expected <= option_strings, command

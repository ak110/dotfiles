"""`atk`のコマンド木全体のヘルプ契約を検証する。"""

from __future__ import annotations

import argparse
import inspect
from collections.abc import Iterator

import _atk_help
import _managed_temp
import atk
import pytest

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


def test_add_command_requires_summary_and_description() -> None:
    parameters = inspect.signature(_atk_help.add_command).parameters

    for name in ("summary", "description"):
        assert parameters[name].kind is inspect.Parameter.KEYWORD_ONLY
        assert parameters[name].default is inspect.Parameter.empty


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


@pytest.mark.parametrize("argv", [[], ["wi"], ["plans"], ["managed-temp"], ["review-table"]])
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
        value = str(next(iter(action.choices))) if action.choices else "value"
        if action.option_strings:
            if action.required:
                option = next(option for option in action.option_strings if option.startswith("--"))
                arguments.extend((option, value))
            continue
        if action.nargs not in ("?", "*"):
            arguments.append(value)
    return arguments, parser


def test_every_subcommand_rejects_unsupported_argument_with_accepted_options(
    capsys: pytest.CaptureFixture[str],
) -> None:
    for command, parser, _summary in _walk_commands():
        if command == "atk":
            continue
        arguments, target = _required_arguments(parser)
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
    worktree_stash_help = commands["atk worktree-stash"].format_help()
    assert "refs/worktree/<ラベル>" in worktree_stash_help


def test_worktree_stash_help_covers_save_restore_and_drop() -> None:
    commands = {command: parser for command, parser, _summary in _walk_commands()}
    help_text = commands["atk worktree-stash"].format_help()

    assert "atk worktree-stash save --label <ラベル>" in help_text
    assert "git stash apply --index refs/worktree/<ラベル>" in help_text
    assert "atk worktree-stash drop refs/worktree/<ラベル>" in help_text


def test_managed_temp_create_help_lists_all_prefix_rules() -> None:
    commands = {command: parser for command, parser, _summary in _walk_commands()}
    help_text = commands["atk managed-temp create"].format_help()

    for description, _satisfied in _managed_temp._PREFIX_RULES:  # pylint: disable=protected-access
        assert description in help_text

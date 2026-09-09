"""`atk`のコマンド木全体のヘルプ契約を検証する。"""

from __future__ import annotations

import argparse
import inspect
from collections.abc import Iterator

import pytest

from agent_toolkit import atk
from agent_toolkit._atk import help_text as _atk_help
from agent_toolkit._atk import managed_temp as _managed_temp

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


def test_worktree_stash_help_covers_save_restore_and_drop() -> None:
    commands = {command: parser for command, parser, _summary in _walk_commands()}
    help_text = commands["atk worktree-stash"].format_help()

    assert "atk worktree-stash save --label <ラベル>" in help_text
    assert "git stash apply --index refs/worktree/<ラベル>" in help_text
    assert "atk worktree-stash drop refs/worktree/<ラベル>" in help_text


def test_wait_schedule_help_explains_request_bucket_resolution() -> None:
    """wait-scheduleはbucket指定がTTLとcron式の解決に必要な理由を示す。"""
    commands = {command: parser for command, parser, _summary in _walk_commands()}
    description = commands["atk wait-schedule"].description

    assert description is not None
    assert "呼び出し主体のbucketを待機TTLとcron式の解決へ入力" in description
    assert "呼び出し主体のbucketを自動解決できない" in description


def test_wi_edit_help_explains_agent_environment_processing_restriction() -> None:
    """wi editはエージェント環境でprocessingの本文置換だけを拒否すると示す。"""
    commands = {command: parser for command, parser, _summary in _walk_commands()}
    help_text = commands["atk wi edit"].format_help()

    assert "processingの項目の本文置換を拒否する" in help_text
    assert "--appendによる追記は拒否しない" in help_text


def test_managed_temp_create_help_lists_all_prefix_rules() -> None:
    commands = {command: parser for command, parser, _summary in _walk_commands()}
    help_text = commands["atk managed-temp create"].format_help()

    for description, _satisfied in _managed_temp._PREFIX_RULES:  # pylint: disable=protected-access
        assert description in help_text


def test_managed_temp_cleanup_help_explains_force_remove_boundary() -> None:
    """cleanupは強制回収で維持する最低限の検証条件を示す。"""
    commands = {command: parser for command, parser, _summary in _walk_commands()}
    help_text = commands["atk managed-temp cleanup"].format_help()

    assert "--force-remove" in help_text
    assert "一時rootの直下" in help_text
    assert "現在の利用者が所有するディレクトリ" in help_text


def test_review_table_init_help_describes_dialogue_review_table() -> None:
    commands = {command: parser for command, parser, _summary in _walk_commands()}
    parser = commands["atk review-table init"]
    help_text = parser.format_help()

    assert "対話由来の小規模是正の実行レビュー表" in help_text
    assert "dlg-<実装着手前の完全OID>.exec-review.tsv" in help_text
    assert "実装着手前の完全OID由来の`dlg-<OID>.exec-review.tsv`" in help_text


def test_plans_checkout_help_describes_remote_sync_side_effects() -> None:
    commands = {command: parser for command, parser, _summary in _walk_commands()}
    description = commands["atk plans checkout"].description

    assert description is not None
    assert "未送信commitをremoteへpush" in description
    assert "remoteの変更をprivate-notesへpull" in description
    assert "private-notesの内容は変更しない" not in description


@pytest.mark.parametrize(
    ("command", "format_name"),
    [
        ("atk wi list", "JSON Lines"),
        ("atk plans list", "TSV"),
        ("atk agents-wait", "単一のJSON文書"),
        ("atk managed-temp list", "JSON Lines"),
        ("atk review-table show", "raw TSV"),
    ],
)
def test_structured_output_commands_state_their_format(command: str, format_name: str) -> None:
    """構造化出力を返すコマンドは解析形式を一意に明示する。"""
    commands = {name: parser for name, parser, _summary in _walk_commands()}
    description = commands[command].description

    assert description is not None
    assert format_name in description

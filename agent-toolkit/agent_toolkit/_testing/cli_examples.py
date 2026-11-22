from __future__ import annotations

import argparse
import contextlib
import io
import pathlib
import re
from collections.abc import Iterator

import pytest

from agent_toolkit import atk
from agent_toolkit._atk import run_script
from agent_toolkit._common.shell_segments import split_bash_commands
from agent_toolkit._common.shell_tokens import split_words, strip_redirections


class _ParserCaptured(Exception):
    def __init__(self, parser: argparse.ArgumentParser) -> None:
        super().__init__(parser.prog)
        self.parser = parser


def registered_parser(script_name: str) -> argparse.ArgumentParser:

    def capture(self: argparse.ArgumentParser, *args: object, **kwargs: object) -> argparse.Namespace:
        del args, kwargs
        raise _ParserCaptured(self)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(argparse.ArgumentParser, "parse_args", capture)
        try:
            run_script.dispatch(argparse.Namespace(script_name=script_name, script_args=[]))
        except _ParserCaptured as captured:
            return captured.parser
    raise AssertionError(f"登録スクリプトがparserを構築しなかった: {script_name}")


def examples(text: str) -> Iterator[tuple[int, str]]:
    pending = ""
    start = 0
    for number, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if pending:
            pending += " " + line.removesuffix("\\").strip()
        elif line.startswith("atk "):
            start = number
            pending = line.removesuffix("\\").strip()
        else:
            continue
        if line.endswith("\\"):
            continue
        yield start, pending
        pending = ""
    assert not pending, f"未完の継続行: {start}"


def example_argv(command: str) -> list[str]:
    command = re.sub(r"\$\([^()]*\)", "/absolute/example", command)
    command = re.sub(r"<(?:round|ラウンド番号|正の有限秒数|秒数|N)>", "1", command)
    command = command.replace("<40桁のSHA>", "a" * 40)
    command = re.sub(r"<[^<>]+>", "example", command)

    commands = split_bash_commands(command)
    if not commands:
        return []
    segment = commands[0][1]
    words = split_words(segment)
    comment = next((index for index, word in enumerate(words) if segment[word.start] == "#"), len(words))
    return list(strip_redirections(words[:comment]))


def validate_example(command: str, *, source: str, parsers: dict[str, argparse.ArgumentParser] | None = None) -> None:
    argv = example_argv(command)
    assert argv and argv[0] == "atk", (source, command)
    diagnostic = io.StringIO()
    with contextlib.redirect_stderr(diagnostic), contextlib.redirect_stdout(io.StringIO()):
        try:
            root = parsers.get("atk") if parsers is not None else None
            if root is None:
                root = atk._build_parser()  # pylint: disable=protected-access
                if parsers is not None:
                    parsers["atk"] = root
            args = root.parse_args(argv[1:])
            if args.command == "run-script":
                script = args.script_name
                parser = parsers.get(script) if parsers is not None else None
                if parser is None:
                    parser = registered_parser(script)
                    if parsers is not None:
                        parsers[script] = parser
                script_args = args.script_args
                if script_args[:1] == ["--"]:
                    script_args = script_args[1:]
                parser.parse_args(script_args)
        except SystemExit as error:
            assert error.code == 0, f"{source}: {command}\n{diagnostic.getvalue()}"


def validate_document(path: pathlib.Path, *, parsers: dict[str, argparse.ArgumentParser] | None = None) -> int:
    count = 0
    for line, command in examples(path.read_text(encoding="utf-8")):
        validate_example(command, source=f"{path}:{line}", parsers=parsers)
        count += 1
    return count

"""`push-and-ci.md`が示すwait_ciの起動例を、wait_ciの引数解析へ通す不変条件テスト。

手順書の起動例とスクリプトの引数定義は別々に編集されるため、一方だけを変えると手順どおりの起動が失敗する。
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import shlex
from typing import Any, NoReturn
from unittest import mock

import pytest

from agent_toolkit import wait_ci

_DOCUMENT = pathlib.Path(__file__).resolve().parents[1] / "skills/commit/references/push-and-ci.md"


class _ParsedExample(Exception):
    """引数解析の成功後に起動例の処理を止めるための例外。"""


def _documented_wait_ci_examples() -> list[str]:
    """`push-and-ci.md`が示す`atk run-script wait-ci --`の起動例を、行の継続を結合して返す。"""
    text = re.sub(r"\\\n\s*", " ", _DOCUMENT.read_text(encoding="utf-8"))
    examples = re.findall(r"atk run-script wait-ci -- [^`\n]+", text)
    # `--help`の案内は受理形式の確認手段であり、待機・baseline作成の起動例ではない。
    return [example for example in examples if "--help" not in example.split()]


def _parse_documented_example(example: str, tmp_path: pathlib.Path) -> argparse.Namespace:
    """起動例の`<…>`を実在する形の値へ置き換え、wait_ciの引数解析と引数の組合せの判定だけを通す。"""
    replacements = {
        "<owner/repo>": "owner/repository",
        "<destination-ref>": "refs/heads/main",
        "<source-ref>": "HEAD",
        "<40桁のSHA>": "a" * 40,
    }
    tokens = []
    for token in shlex.split(example.removeprefix("atk run-script wait-ci --")):
        for placeholder, value in replacements.items():
            token = token.replace(placeholder, value)
        tokens.append(re.sub(r"<[^>]+>", str(tmp_path / "placeholder.json"), token))
    captured: list[argparse.Namespace] = []

    def stop_after_checks(*_args: Any, **_kwargs: Any) -> NoReturn:
        raise _ParsedExample

    original = argparse.ArgumentParser.parse_args

    def parse_args(self: argparse.ArgumentParser, *args: Any, **kwargs: Any) -> argparse.Namespace:
        namespace = original(self, *args, **kwargs)
        captured.append(namespace)
        return namespace

    (tmp_path / "placeholder.json").write_text(
        json.dumps(
            {
                "version": 2,
                "forge": "github",
                "repository": "owner/repository",
                "ref": "refs/heads/main",
                "source_ref": "HEAD",
                "sha": "a" * 40,
                "run_ids": [],
            }
        ),
        encoding="utf-8",
    )
    # 引数の組合せの判定はparse_argsの後にあるため、待機と書込の手前で止めて受理だけを確かめる。
    with (
        mock.patch.object(argparse.ArgumentParser, "parse_args", parse_args),
        mock.patch.object(wait_ci, "_wait_from_record", stop_after_checks),
        mock.patch.object(wait_ci, "_resolve_sha", stop_after_checks),
        pytest.raises(_ParsedExample),
    ):
        wait_ci.main(tokens)
    return captured[0]


def test_documented_examples_are_accepted_by_parser(tmp_path: pathlib.Path) -> None:
    """手順書の起動例がwait_ciの受理形式で受理される。起動例が見つからない場合も失敗させる。"""
    examples = _documented_wait_ci_examples()
    assert len(examples) >= 3, examples
    for example in examples:
        _parse_documented_example(example, tmp_path)


def test_documented_examples_cover_all_modes() -> None:
    """相互排他で宣言した各起動形式に、引数解析へ渡せる実行例がある。"""
    modes: set[str] = set()

    def inspect_parser(parser: argparse.ArgumentParser, *_args: Any, **_kwargs: Any) -> NoReturn:
        for group in parser._mutually_exclusive_groups:  # pylint: disable=protected-access
            for action in group._group_actions:  # pylint: disable=protected-access
                modes.update(action.option_strings)
        raise _ParsedExample

    with mock.patch.object(argparse.ArgumentParser, "parse_args", inspect_parser), pytest.raises(_ParsedExample):
        wait_ci.main([])
    documented = {token for example in _documented_wait_ci_examples() for token in shlex.split(example)}
    assert modes
    assert not modes - documented, f"完全な実行例が無い起動形式: {modes - documented}"

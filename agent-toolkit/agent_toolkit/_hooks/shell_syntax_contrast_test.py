"""引用・エスケープで同じ意味になる入力と、演算子で意味が変わる入力を、共通表現と各消費側の解析APIで対照する。

各消費側が構文を別々に解釈すると、引用した演算子を実際の区切りやリダイレクトと取り違える。
同じ対照入力を区間解析、取得計画、待機回収、検証コマンド、CLI例の各判定へ通し、
同じ意味の表記は同じ結果に、異なる意味の演算子は用途ごとの受理条件どおりの結果になることを確かめる。
公開hook入力から最終結果（警告、取得記録、待機回収）までの対照は、各消費側の試験に置く。
"""

from __future__ import annotations

import pytest

from agent_toolkit._common import shell_segments
from agent_toolkit._common.next_action import ActionableError
from agent_toolkit._common.shell_tokens import is_agents_wait_command, join_words, split_words, strip_redirections
from agent_toolkit._hooks import codex_read_observation
from agent_toolkit._plan.structure import verification
from agent_toolkit._testing import cli_examples


@pytest.mark.parametrize(
    "command",
    ["cat 'a;b>c|d.md'", 'cat "a;b>c|d.md"', "cat a\\;b\\>c\\|d.md", "cat 'a;'b'>c|d.md'"],
)
def test_quoted_operators_are_data_for_every_consumer(command: str) -> None:
    """引用・エスケープした演算子は1つの語の値になり、区切りやリダイレクトとして扱われない。"""
    segments = shell_segments.extract_execution_segments(command)
    assert len(segments) == 1
    assert strip_redirections(segments[0].tokens) == ("cat", "a;b>c|d.md")
    assert codex_read_observation.read_command_groups(command, read_prefix=True) == [["cat", "a;b>c|d.md"]]
    plan = codex_read_observation.read_plan(command)
    assert [read.argv for read in plan] == [("cat", "a;b>c|d.md")]
    assert tuple(split_words(join_words(segments[0].tokens))) == segments[0].tokens


@pytest.mark.parametrize(
    ("command", "groups", "prefix_groups", "plan_reads"),
    [
        pytest.param("cat a.md && cat b.md", 2, 2, 2, id="and-chain-is-a-read"),
        pytest.param("cat a.md; cat b.md", 2, None, 2, id="semicolon-hides-failure"),
        pytest.param("cat a.md\ncat b.md", 2, None, 2, id="newline-hides-failure"),
        pytest.param("cat a.md | head", None, None, 0, id="pipe-loses-output"),
        pytest.param("cat a.md > out", None, None, 0, id="redirect-loses-output"),
        pytest.param("cat a.md 2>&1", None, None, 0, id="fd-redirect"),
        pytest.param("cat a.md || true", None, None, 0, id="or-hides-failure"),
        pytest.param('cat "$TARGET"', 1, None, 0, id="expansion"),
        pytest.param("cat '$TARGET'", 1, 1, 1, id="single-quoted-dollar-is-literal"),
    ],
)
def test_operators_change_read_acquisition(
    command: str, groups: int | None, prefix_groups: int | None, plan_reads: int
) -> None:
    """実際の連結・パイプ・リダイレクト・展開は、取得記録と警告候補の受理条件どおりに分かれる。

    取得記録（`read_prefix`）は単独と`&&`連結の静的読取だけを受理し、`;`・改行による失敗の隠蔽、
    出力の喪失、展開を含む入力を受理しない。警告候補の区切り（`read_prefix`なし）は`;`と改行も受理する。
    """
    result = codex_read_observation.read_command_groups(command)
    assert (None if result is None else len(result)) == groups
    prefix = codex_read_observation.read_command_groups(command, read_prefix=True)
    assert (None if prefix is None else len(prefix)) == prefix_groups
    assert len(codex_read_observation.read_plan(command)) == plan_reads


@pytest.mark.parametrize(
    ("command", "arguments"),
    [
        ("echo '>' out", ("echo", ">", "out")),
        ("echo \\> out", ("echo", ">", "out")),
        ("echo > out", ("echo",)),
        ("echo >out", ("echo",)),
        ("echo 2>&1 x", ("echo", "x")),
        ("echo '2>&1' x", ("echo", "2>&1", "x")),
    ],
)
def test_redirections_are_removed_only_when_unquoted(command: str, arguments: tuple[str, ...]) -> None:
    """引数を数える消費側は、引用した`'>'`を引数として残し、実際のリダイレクトだけを除く。"""
    (segment,) = shell_segments.extract_execution_segments(command)
    assert strip_redirections(segment.tokens) == arguments


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("atk agents wait", True),
        ("timeout 60 atk agents wait", True),
        ("bash -c 'atk agents wait'", True),
        ("echo 'atk agents wait'", False),
        ("echo atk agents wait", False),
        ("printf '%s' 'atk agents wait'; true", False),
    ],
)
def test_wait_collection_uses_segment_execution_positions(command: str, *, expected: bool) -> None:
    """待機回収は実行位置の語だけを比べ、引用した文字列中の同じ語を起動と誤認しない。"""
    found = any(
        segment.resolved and is_agents_wait_command(segment.tokens)
        for segment in shell_segments.extract_execution_segments(command)
    )
    assert found is expected


@pytest.mark.parametrize(
    ("cell", "argv"),
    [
        ("`echo '|' x`", ["echo", "|", "x"]),
        ("`echo 'a;b' x`", ["echo", "a;b", "x"]),
        ("`grep -n 'x>y' file`", ["grep", "-n", "x>y", "file"]),
    ],
)
def test_verification_commands_accept_quoted_operators(cell: str, argv: list[str]) -> None:
    """検証コマンドの限定文法は、引用した演算子をargvの値として受理する。"""
    assert [command[1] for command in verification.commands_from_cell(cell)] == [argv]


@pytest.mark.parametrize("cell", ["`echo | x`", "`echo > out`", "`echo a; echo b`", '`echo "$HOME"`', "`FOO=1 echo`"])
def test_verification_commands_reject_shell_reevaluation(cell: str) -> None:
    """引用の外側の区切り・リダイレクト・展開・前置代入はshellの再評価を要するため拒否する。"""
    with pytest.raises(ActionableError, match="shellの再評価"):
        verification.commands_from_cell(cell)


@pytest.mark.parametrize(
    ("command", "argv"),
    [
        ("atk wi show 'a|b' | head", ["atk", "wi", "show", "a|b"]),
        ("atk wi show x > out", ["atk", "wi", "show", "x"]),
        ("atk wi show '>' # comment", ["atk", "wi", "show", ">"]),
        ("atk wi show x && echo done", ["atk", "wi", "show", "x"]),
    ],
)
def test_cli_examples_keep_quoted_operators(command: str, argv: list[str]) -> None:
    """CLI例の検査は、引用した演算子を引数として残し、実際のパイプ・リダイレクト・連結・コメントを除く。"""
    assert cli_examples.example_argv(command) == argv

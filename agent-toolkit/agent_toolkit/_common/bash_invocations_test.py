"""Bash複合文の実行位置と、囲む文から継承する出力接続を検証する。"""

from __future__ import annotations

import pytest

from agent_toolkit._common.bash_invocations import extract_bash_invocations


@pytest.mark.parametrize(
    "command, names",
    [
        ("for item in one two; do atk wi list; done", ["atk"]),
        ("for ((i=0;i<2;i++)); do atk wi list; done", ["atk"]),
        ("select item in one two; do atk wi list; done", ["atk"]),
        ("while test -f marker; do atk wi list; done", ["test", "atk"]),
        ("until test -f marker; do atk wi list; done", ["test", "atk"]),
        (
            "if test -f marker; then atk wi list; elif test -d marker; then git status; else cat marker; fi",
            ["test", "atk", "test", "git", "cat"],
        ),
        ("case value in one|two) atk wi list;; *) git status;; esac", ["atk", "git"]),
        (
            "for item in one; do if test -f marker; then while test -d marker; do atk wi list; done; fi; done",
            ["test", "test", "atk"],
        ),
        ("printf '%s' 'if atk wi list; then'; cat <<'END'\natk wi list\nEND\n", ["printf", "cat"]),
        ("unused() { atk wi list; }; git status", ["git"]),
        ("function unused { atk wi list; }; git status", ["git"]),
        ("function unused() { atk wi list; }; git status", ["git"]),
        ("unused()\n{ atk wi list; }; git status", ["git"]),
        ("unused() (atk wi list); git status", ["git"]),
        ("case value in (one) atk wi list;& two) git status;;& *) cat marker;; esac", ["atk", "git", "cat"]),
        ("((atk++)); git status", ["git"]),
        ("for item in one; do $command wi list; done", []),
        ("for item in one; do atk wi list", []),
        ("if test -f marker; then atk wi list", []),
        ("while test -f marker; do atk wi list", []),
        ("case value in one) atk wi list", []),
        ("unused() { atk wi list", []),
    ],
)
def test_compound_execution_positions(command: str, names: list[str]) -> None:
    """実行され得る条件と本体だけを抽出し、データや不確定な構文を補完しない。"""
    assert [item.segment.tokens[0] for item in extract_bash_invocations(command)] == names


@pytest.mark.parametrize("connection", ["| cat", "|& cat", "> output.log", "2> errors.log", "&"])
def test_compound_inherits_output_connections(connection: str) -> None:
    """文全体の接続は入れ子の条件と本体へ作用する。"""
    calls = extract_bash_invocations(f"if test -f marker; then for item in one; do atk wi list; done; fi {connection}")
    inner = [item for item in calls if item.segment.tokens[0] in {"test", "atk"}]
    assert len(inner) == 2
    for item in inner:
        assert item.background == (connection == "&")
        assert item.output_pipe == connection.startswith("|")
        assert item.output_redirected == (connection != "&")

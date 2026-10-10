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


@pytest.mark.parametrize(
    "prefix",
    [
        "time",
        "time -p",
        "time --",
        "time -p --",
        "!",
        "! time -p",
        "! time -p --",
        "time !",
        "! !",
        "time time -p",
        "time -- time -p --",
    ],
)
@pytest.mark.parametrize(
    "body, names",
    [
        ("atk wi list", ["atk"]),
        ("for item in one; do atk wi list; done", ["atk"]),
        ("for ((i=0;i<2;i++)); do atk wi list; done", ["atk"]),
        ("select item in one; do atk wi list; done", ["atk"]),
        ("while test -f marker; do atk wi list; done", ["test", "atk"]),
        ("until test -f marker; do atk wi list; done", ["test", "atk"]),
        ("if test -f marker; then atk wi list; fi", ["test", "atk"]),
        ("case value in one) atk wi list;; esac", ["atk"]),
        ("{ atk wi list; }", ["atk"]),
        ("(atk wi list)", ["atk"]),
        ("unused() { atk wi list; }", []),
        ("for item in one; do ! if test -f marker; then atk wi list; fi; done", ["test", "atk"]),
    ],
)
def test_reserved_pipeline_prefix_preserves_execution_positions(prefix: str, body: str, names: list[str]) -> None:
    """文種を前置語の有無で変えず、定義しただけの関数を実行扱いにしない。"""
    calls = extract_bash_invocations(f"{prefix} {body}")
    assert [item.segment.tokens[0] for item in calls] == names
    assert all(item.arguments_known for item in calls)


@pytest.mark.parametrize("prefix", ["time -p", "time --", "time -p --", "! time", "coproc", "coproc WORKER"])
@pytest.mark.parametrize("connection", ["", "| cat", "|& cat", "> output.log", "2> errors.log", "&"])
def test_prefixed_compound_connections(prefix: str, connection: str) -> None:
    """外側の接続とcoprocの暗黙パイプ・非同期を条件と本体へ継承する。"""
    calls = extract_bash_invocations(f"{prefix} if test -f marker; then atk wi list; fi {connection}")
    inner = [item for item in calls if item.segment.tokens[0] in {"test", "atk"}]
    assert len(inner) == 2
    coprocess = prefix.startswith("coproc")
    for item in inner:
        assert item.background == (coprocess or connection == "&")
        assert item.output_pipe == (connection.startswith("|") or coprocess and connection != "> output.log")
        assert item.output_redirected == (coprocess or connection not in {"", "&"})


@pytest.mark.parametrize(
    "command, names",
    [
        ("coproc atk wi list", ["atk"]),
        ("coproc time -p for item in one; do atk wi list; done", ["atk"]),
        ("coproc WORKER { atk wi list; }", ["atk"]),
        ("coproc WORKER (atk wi list)", ["atk"]),
        ("coproc WORKER unused() { atk wi list; }", []),
        ("'time' atk wi list", ["time"]),
        ("\\! atk wi list", ["!"]),
        ("/usr/bin/time atk wi list", ["/usr/bin/time"]),
        ("command time atk wi list", ["time"]),
        ("printf '%s' 'time ! coproc'; atk wi list", ["printf", "atk"]),
        ("time", []),
        ("! if test -f marker; then atk wi list", []),
        ("time for item in one; do $command wi list; done", []),
    ],
)
def test_prefix_names_and_non_prefix_inputs(command: str, names: list[str]) -> None:
    """coprocの任意名と、外部名・引用・引数・不確定な入力を区別する。"""
    assert [item.segment.tokens[0] for item in extract_bash_invocations(command)] == names


@pytest.mark.parametrize("prefix", ["time --", "time -p --"])
def test_time_option_terminator_preserves_arguments(prefix: str) -> None:
    """オプション終端を前置へ収め、呼出名と静的引数を保持する。"""
    calls = extract_bash_invocations(f"{prefix} git status")
    assert len(calls) == 1
    assert calls[0].segment.tokens == ("git", "status")
    assert calls[0].static_tokens == ("git", "status")
    assert calls[0].arguments_known

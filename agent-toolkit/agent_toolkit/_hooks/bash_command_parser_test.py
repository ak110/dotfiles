"""agent-toolkit/agent_toolkit/_hooks/bash_command_parser.py のテスト。

引用の走査、実行位置の抽出及びセグメント分割の挙動を検証する。
"""

from __future__ import annotations

import pytest

from agent_toolkit._hooks.bash_command_parser import (  # noqa: E402  # pylint: disable=wrong-import-position,import-error
    ExecutionSegment,
    QuotingScanner,
    extract_execution_segments,
    mask_heredoc_bodies,
    split_bash_segments,
)

_TOOLKIT_PREFIX = "agent-" + "toolkit"


def _scan_unquoted(text: str) -> tuple[list[int], str | None]:
    """`QuotingScanner`で引用の外側の文字位置と、走査後に残る引用を返す。"""
    scanner = QuotingScanner(text)
    positions: list[int] = []
    while scanner.index < len(text):
        if scanner.consume_quoted():
            continue
        char = text[scanner.index]
        if char in {"'", '"'}:
            scanner.enter_quote(char)
            continue
        positions.append(scanner.index)
        scanner.index += 1
    return positions, scanner.quote


class TestQuotingScanner:
    """引用とエスケープの状態を保つ共有走査。"""

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("a|b", [0, 1, 2]),
            ("a'b|c'd", [0, 6]),
            ('a"b|c"d', [0, 6]),
            ("a\\|b", [0, 3]),
            ("a\\'b|c", [0, 3, 4, 5]),
            ("'a\\'b", [4]),
            ("", []),
        ],
    )
    def test_quoted_and_escaped_positions_are_consumed(self, text: str, expected: list[int]) -> None:
        positions, quote = _scan_unquoted(text)
        assert positions == expected
        assert quote is None

    @pytest.mark.parametrize("text", ["a'b", 'a"b', 'a"b\\"'])
    def test_unterminated_quote_remains_after_scan(self, text: str) -> None:
        _, quote = _scan_unquoted(text)
        assert quote is not None


class TestExtractExecutionSegments:
    """助言・状態記録が共有するBash実行位置の抽出。"""

    @pytest.mark.parametrize(
        ("command", "expected", "raw"),
        [
            (
                "/repo/create_plan_files.py a b",
                ("/repo/create_plan_files.py", "a", "b"),
                ("/repo/create_plan_files.py", "a", "b"),
            ),
            (
                "uv run --no-project --script /repo/create_plan_files.py a b",
                ("/repo/create_plan_files.py", "a", "b"),
                ("uv", "run", "--no-project", "--script", "/repo/create_plan_files.py", "a", "b"),
            ),
            ("echo create_plan_files.py", ("echo", "create_plan_files.py"), ("echo", "create_plan_files.py")),
        ],
    )
    def test_execution_position_is_resolved(self, command: str, expected: tuple[str, ...], raw: tuple[str, ...]) -> None:
        assert extract_execution_segments(command) == [ExecutionSegment(expected, True, False, raw)]

    def test_shell_command_is_expanded_once(self) -> None:
        segments = extract_execution_segments("bash -lc 'uv run --script /repo/create_plan_files.py'")
        assert segments == [
            ExecutionSegment(
                ("/repo/create_plan_files.py",),
                True,
                False,
                ("uv", "run", "--script", "/repo/create_plan_files.py"),
            )
        ]

    def test_agent_toolkit_project_entry_is_identified(self) -> None:
        command = "uv run --project /repo/agent-toolkit --locked --no-default-groups /repo/agent-toolkit/agent_toolkit/hook.py"
        assert extract_execution_segments(command) == [
            ExecutionSegment(
                ("/repo/agent-toolkit/agent_toolkit/hook.py",),
                True,
                True,
                (
                    "uv",
                    "run",
                    "--project",
                    "/repo/agent-toolkit",
                    "--locked",
                    "--no-default-groups",
                    "/repo/agent-toolkit/agent_toolkit/hook.py",
                ),
            )
        ]

    @pytest.mark.parametrize("name", ("atk_serve_plans_remote_helper.py", "atk_serve_sessions_remote_helper.py"))
    def test_remote_helper_script_is_identified(self, name: str) -> None:
        command = f"uv run --no-project --script /repo/agent-toolkit/scripts/{name}"
        assert extract_execution_segments(command) == [
            ExecutionSegment(
                (f"/repo/agent-toolkit/scripts/{name}",),
                True,
                True,
                ("uv", "run", "--no-project", "--script", f"/repo/agent-toolkit/scripts/{name}"),
            )
        ]

    def test_other_agent_toolkit_pep723_script_is_not_identified(self) -> None:
        script = f"/repo/{_TOOLKIT_PREFIX}/scripts/other.py"
        command = f"uv run --no-project --script {script}"
        assert extract_execution_segments(command) == [
            ExecutionSegment((script,), True, False, ("uv", "run", "--no-project", "--script", script))
        ]


class TestSplitBashSegments:
    """`split_bash_segments`によるセグメント分割。"""

    @pytest.mark.parametrize(
        ("command", "expected"),
        [
            ("git log", ["git log"]),
            ("git log; git status", ["git log", "git status"]),
            ("git log && git status", ["git log", "git status"]),
            ("git log || true", ["git log", "true"]),
            ("git log | head -1", ["git log", "head -1"]),
            ("a & b", ["a", "b"]),
            ("", []),
            ("echo 'a; b' && git log", ["echo 'a; b'", "git log"]),
            ('echo "a&b" ; git log', ['echo "a&b"', "git log"]),
        ],
    )
    def test_split(self, command: str, expected: list[str]) -> None:
        assert split_bash_segments(command) == expected

    def test_heredoc_body_is_masked_and_outside_is_split(self) -> None:
        cases = [
            ("cat <<EOF && echo after\nbody && hidden\nEOF\nrg needle .", ["cat <<EOF", "echo after", "rg needle ."]),
            ("cat <<-'EOF'\n\tbody | hidden\n\tEOF\nkill 123", ["cat <<-'EOF'", "kill 123"]),
            ('cat <<"END"\nbody; hidden\nEND\ngit log', ['cat <<"END"', "git log"]),
            (
                "cat <<FIRST <<'SECOND'\nfirst && hidden\nFIRST\nsecond | hidden\nSECOND\ngit status",
                ["cat <<FIRST <<'SECOND'", "git status"],
            ),
            ("cat <<EOF\nunterminated && hidden", ["cat <<EOF"]),
            ("echo a\\ # <<EOF\nbody && hidden\nEOF\ngit status", ["echo a\\ # <<EOF", "git status"]),
        ]
        for command, expected in cases:
            masked = mask_heredoc_bodies(command)
            assert len(masked) == len(command)
            assert masked.count("\n") == command.count("\n")
            assert split_bash_segments(command) == expected

        here_string = "cat <<< 'value && literal'; git status"
        assert mask_heredoc_bodies(here_string) == here_string
        assert split_bash_segments(here_string) == ["cat <<< 'value && literal'", "git status"]

        for arithmetic in ("echo $((1 << 2)) && git status", "((value << 2)); git status"):
            assert mask_heredoc_bodies(arithmetic) == arithmetic

        comment = "echo ok # <<EOF\nnext command"
        assert mask_heredoc_bodies(comment) == comment

    def test_unescaped_newlines_split_top_level_commands(self) -> None:
        command = "atk review-table --help\necho add\natk review-table add --help"
        assert split_bash_segments(command) == [
            "atk review-table --help",
            "echo add",
            "atk review-table add --help",
        ]

    def test_control_keyword_used_as_argument_does_not_open_a_structure(self) -> None:
        assert split_bash_segments("echo for\ngit status") == ["echo for", "git status"]

    def test_line_continuation_and_nested_newlines_are_not_split(self) -> None:
        assert split_bash_segments("echo alpha \\\nbeta") == ["echo alpha \\\nbeta"]
        assert split_bash_segments("echo $(printf 'a\\nb')\necho done") == ["echo $(printf 'a\\nb')", "echo done"]

    @pytest.mark.parametrize(
        "command",
        [
            "for item in a b\ndo\necho $item\ndone",
            "while true\ndo\necho wait\ndone",
            "if true\nthen\necho yes\nfi",
            "case x in\nx) echo yes ;;\nesac",
        ],
    )
    def test_control_structure_newlines_remain_in_one_outer_segment(self, command: str) -> None:
        assert "\n" in split_bash_segments(command)[0]

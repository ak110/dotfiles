"""agent-toolkit/agent_toolkit/_common/shell_quoting.py のテスト。"""

from __future__ import annotations

import pytest

from agent_toolkit._common.shell_quoting import QuotingScanner


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

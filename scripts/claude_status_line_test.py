"""scripts/claude_status_line.py のテスト。"""

from typing import Any

import claude_status_line
import pytest

RESET = claude_status_line.RESET
RED = claude_status_line.RED
GREEN = claude_status_line.GREEN
YELLOW = claude_status_line.YELLOW
BLUE = claude_status_line.BLUE
MAGENTA = claude_status_line.MAGENTA
CYAN = claude_status_line.CYAN
GRAY = claude_status_line.GRAY


class TestRender:
    """render関数の各セグメント組み立てを観点別に検証する。"""

    def test_empty_input(self) -> None:
        assert claude_status_line.render({}) == ""

    @pytest.mark.parametrize(
        ("data", "expected_label"),
        [
            ({"model": {"display_name": "opus"}}, "opus"),
            ({"effort": {"level": "high"}}, "high"),
            (
                {
                    "model": {"display_name": "opus"},
                    "effort": {"level": "xhigh"},
                },
                "opus|xhigh",
            ),
        ],
    )
    def test_model_effort_segment(self, data: dict[str, Any], expected_label: str) -> None:
        assert claude_status_line.render(data) == f"{CYAN}[{expected_label}]{RESET}"

    @pytest.mark.parametrize("invalid", [None, [], 42, ""])
    def test_model_invalid_field_treated_as_missing(self, invalid: Any) -> None:
        data = {"model": {"display_name": invalid}}
        assert claude_status_line.render(data) == ""

    def test_cwd_home_shortened(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HOME", "/home/test")
        data = {"workspace": {"current_dir": "/home/test/projects/foo"}}
        assert claude_status_line.render(data) == f"{BLUE}~/projects/foo{RESET}"

    def test_cwd_exact_home(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HOME", "/home/test")
        data = {"workspace": {"current_dir": "/home/test"}}
        assert claude_status_line.render(data) == f"{BLUE}~{RESET}"

    def test_cwd_outside_home(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HOME", "/home/test")
        data = {"workspace": {"current_dir": "/tmp/repo"}}
        assert claude_status_line.render(data) == f"{BLUE}/tmp/repo{RESET}"

    @pytest.mark.parametrize(
        ("session_id", "expected_visible"),
        [
            ("abcd1234", "abcd1234"),
            ("abcd12345", "abcd1234"),
            ("abcd12345678ef", "abcd1234"),
            ("abc", "abc"),
        ],
    )
    def test_session_id_truncated(self, session_id: str, expected_visible: str) -> None:
        data = {"session_id": session_id}
        assert claude_status_line.render(data) == f"{GRAY}({expected_visible}){RESET}"

    @pytest.mark.parametrize("invalid", [None, "", 42, [], {}])
    def test_session_id_invalid_omitted(self, invalid: Any) -> None:
        data = {"session_id": invalid}
        assert claude_status_line.render(data) == ""

    def test_output_style_named(self) -> None:
        data = {"output_style": {"name": "Explanatory"}}
        assert claude_status_line.render(data) == f"{MAGENTA}@Explanatory{RESET}"

    @pytest.mark.parametrize("name", ["default", "", None, 42, []])
    def test_output_style_omitted(self, name: Any) -> None:
        data = {"output_style": {"name": name}}
        assert claude_status_line.render(data) == ""

    @pytest.mark.parametrize(
        ("value", "expected_color"),
        [
            (0, GREEN),
            (49, GREEN),
            (50, GREEN),
            (51, YELLOW),
            (79, YELLOW),
            (80, YELLOW),
            (81, RED),
            (95, RED),
        ],
    )
    def test_context_percentage_color_threshold(self, value: float, expected_color: str) -> None:
        data = {"context_window": {"used_percentage": value}}
        assert claude_status_line.render(data) == (f"{expected_color}ctx {value:.0f}%{RESET}")

    @pytest.mark.parametrize(
        ("value", "expected_color"),
        [
            (0, GREEN),
            (49, GREEN),
            (50, GREEN),
            (51, YELLOW),
            (79, YELLOW),
            (80, YELLOW),
            (81, RED),
            (95, RED),
        ],
    )
    def test_five_hour_percentage_color_threshold(self, value: float, expected_color: str) -> None:
        data = {"rate_limits": {"five_hour": {"used_percentage": value}}}
        assert claude_status_line.render(data) == (f"{expected_color}5h:{value:.0f}%{RESET}")

    def test_null_numeric_fields_omitted(self) -> None:
        data = {
            "context_window": {"used_percentage": None},
            "cost": {"total_cost_usd": None, "total_duration_ms": None},
            "rate_limits": {"five_hour": {"used_percentage": None}},
        }
        assert claude_status_line.render(data) == ""

    def test_cost_formatting(self) -> None:
        data = {"cost": {"total_cost_usd": 0.1234}}
        assert claude_status_line.render(data) == f"{GRAY}$0.12{RESET}"

    @pytest.mark.parametrize(
        ("ms", "expected"),
        [
            (30 * 1000, "0:30"),
            (12 * 60 * 1000 + 34 * 1000, "12:34"),
            (3600 * 1000, "1:00:00"),
            (3661 * 1000, "1:01:01"),
        ],
    )
    def test_duration_formatting(self, ms: int, expected: str) -> None:
        data = {"cost": {"total_duration_ms": ms}}
        assert claude_status_line.render(data) == f"{GRAY}{expected}{RESET}"

    def test_all_fields_combined(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HOME", "/home/test")
        data = {
            "session_id": "abcd1234efgh5678",
            "model": {"display_name": "claude-opus-4-7"},
            "effort": {"level": "xhigh"},
            "workspace": {"current_dir": "/home/test/dotfiles"},
            "output_style": {"name": "Explanatory"},
            "context_window": {"used_percentage": 42},
            "cost": {"total_cost_usd": 0.12, "total_duration_ms": 754000},
            "rate_limits": {"five_hour": {"used_percentage": 60}},
        }
        expected = (
            f"{CYAN}[claude-opus-4-7|xhigh]{RESET} "
            f"{BLUE}~/dotfiles{RESET} "
            f"{GRAY}(abcd1234){RESET} "
            f"{MAGENTA}@Explanatory{RESET}"
            f" | {GREEN}ctx 42%{RESET}"
            f" | {GRAY}$0.12{RESET}"
            f" | {GRAY}12:34{RESET}"
            f" | {YELLOW}5h:60%{RESET}"
        )
        assert claude_status_line.render(data) == expected

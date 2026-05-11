"""scripts/claude_status_line.py のテスト。"""

from typing import Any

import claude_status_line
import pytest

RESET = claude_status_line.RESET
RED = claude_status_line.RED
GREEN = claude_status_line.GREEN
YELLOW = claude_status_line.YELLOW
BLUE = claude_status_line.BLUE
CYAN = claude_status_line.CYAN
GRAY = claude_status_line.GRAY


@pytest.fixture(autouse=True)
def _stub_git(monkeypatch: pytest.MonkeyPatch) -> None:
    """git呼び出しを既定でgit未管理扱いへスタブする。個別テストで上書き可。"""
    monkeypatch.setattr(claude_status_line, "_git_branch", lambda cwd: None)
    monkeypatch.setattr(claude_status_line, "_git_numstat", lambda cwd: (0, 0))


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

    def test_branch_without_changes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(claude_status_line, "_git_branch", lambda cwd: "main")
        monkeypatch.setattr(claude_status_line, "_git_numstat", lambda cwd: (0, 0))
        monkeypatch.setenv("HOME", "/home/test")
        data = {"workspace": {"current_dir": "/tmp/repo"}}
        assert claude_status_line.render(data) == (f"{BLUE}/tmp/repo{RESET} {GREEN}main{RESET}")

    def test_branch_with_changes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(claude_status_line, "_git_branch", lambda cwd: "main")
        monkeypatch.setattr(claude_status_line, "_git_numstat", lambda cwd: (12, 3))
        monkeypatch.setenv("HOME", "/home/test")
        data = {"workspace": {"current_dir": "/tmp/repo"}}
        assert claude_status_line.render(data) == (
            f"{BLUE}/tmp/repo{RESET} {GREEN}main{RESET} {GREEN}+12{RESET} {RED}-3{RESET}"
        )

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
        monkeypatch.setattr(claude_status_line, "_git_branch", lambda cwd: "master")
        monkeypatch.setattr(claude_status_line, "_git_numstat", lambda cwd: (12, 3))
        monkeypatch.setenv("HOME", "/home/test")
        data = {
            "model": {"display_name": "claude-opus-4-7"},
            "effort": {"level": "xhigh"},
            "workspace": {"current_dir": "/home/test/dotfiles"},
            "context_window": {"used_percentage": 42},
            "cost": {"total_cost_usd": 0.12, "total_duration_ms": 754000},
            "rate_limits": {"five_hour": {"used_percentage": 60}},
        }
        expected = (
            f"{CYAN}[claude-opus-4-7|xhigh]{RESET} "
            f"{BLUE}~/dotfiles{RESET} {GREEN}master{RESET}"
            f" {GREEN}+12{RESET} {RED}-3{RESET}"
            f" | {GREEN}ctx 42%{RESET}"
            f" | {GRAY}$0.12{RESET}"
            f" | {GRAY}12:34{RESET}"
            f" | {YELLOW}5h:60%{RESET}"
        )
        assert claude_status_line.render(data) == expected

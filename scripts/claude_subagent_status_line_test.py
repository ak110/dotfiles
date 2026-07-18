"""scripts/claude_subagent_status_line.py のテスト。"""

# pylint: disable=protected-access  # 内部ヘルパー関数の単体テスト目的で `_` プレフィックス関数へアクセスする

import datetime
import io
import json
from typing import Any

import claude_subagent_status_line
import pytest

SEP = claude_subagent_status_line._SEP
NOW = datetime.datetime(2026, 1, 1, 0, 0, 0, tzinfo=datetime.UTC)


def render(task: dict[str, Any], width: int = 80) -> str | None:
    return claude_subagent_status_line.render_task(task, width, NOW)


def width_of(text: str) -> int:
    return claude_subagent_status_line._display_width(text)


class TestDisplayWidth:
    """`_display_width`関数の表示幅換算を実装から独立した既知値で検証する。"""

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("a", 1),
            ("あ", 2),
            ("ab", 2),
            ("aあ", 3),
            ("§", 2),
            ("±", 2),
        ],
    )
    def test_known_width(self, text: str, expected: int) -> None:
        assert width_of(text) == expected


class TestRenderTaskLeft:
    """render_task関数の左寄せグループ組み立てを観点別に検証する。"""

    def test_missing_id_returns_none(self) -> None:
        assert render({"name": "foo"}) is None

    @pytest.mark.parametrize("invalid_id", [None, 42, "", []])
    def test_invalid_id_returns_none(self, invalid_id: Any) -> None:
        assert render({"id": invalid_id, "name": "foo"}) is None

    def test_name_only(self) -> None:
        assert render({"id": "t1", "name": "foo"}) == "foo"

    @pytest.mark.parametrize(
        ("model_id", "expected"),
        [
            ("claude-opus-4-8", "Opus"),
            ("claude-sonnet-5", "Sonnet"),
            ("claude-haiku-4-5", "Haiku"),
            ("claude-fable-1", "Fable"),
            ("unknown-model-x", "unknown-model-x"),
        ],
    )
    def test_model_short_name(self, model_id: str, expected: str) -> None:
        assert render({"id": "t1", "name": "foo", "model": model_id}) == f"foo{SEP}{expected}"

    def test_description_included(self) -> None:
        assert render({"id": "t1", "name": "foo", "description": "bar"}) == f"foo{SEP}bar"

    def test_empty_description_omitted(self) -> None:
        assert render({"id": "t1", "name": "foo", "description": "   "}) == "foo"

    def test_description_newlines_collapsed_to_spaces(self) -> None:
        result = render({"id": "t1", "name": "foo", "description": "l1\nl2\r\nl3\rl4   l5"}, 200)
        assert result == f"foo{SEP}l1 l2 l3 l4 l5"

    def test_description_truncated_to_fit_width(self) -> None:
        result = render({"id": "t1", "name": "foo", "description": "x" * 100}, 10)
        assert result is not None
        assert width_of(result) <= 10
        assert result.endswith("…")

    def test_description_exact_fit_not_truncated(self) -> None:
        result = render({"id": "t1", "name": "foo", "description": "x" * 4}, 11)
        assert result == f"foo{SEP}xxxx"

    def test_description_budget_zero_dropped(self) -> None:
        result = render({"id": "t1", "name": "foo", "description": "bar"}, 6)
        assert result == "foo"

    def test_description_budget_ellipsis_only(self) -> None:
        result = render({"id": "t1", "name": "foo", "description": "bar"}, 9)
        assert result == f"foo{SEP}…"

    def test_fullwidth_description_truncated_by_display_width(self) -> None:
        result = render({"id": "t1", "name": "foo", "description": "あ" * 50}, 20)
        assert result is not None
        assert width_of(result) <= 20
        assert result.endswith("…")

    def test_ambiguous_width_description_truncated_by_display_width(self) -> None:
        result = render({"id": "t1", "name": "foo", "description": "§" * 50}, 20)
        assert result is not None
        assert width_of(result) <= 20
        assert result.endswith("…")


class TestRenderTaskRight:
    """render_task関数の右寄せグループと幅充填を観点別に検証する。"""

    def test_token_count_right_aligned(self) -> None:
        result = render({"id": "t1", "name": "foo", "tokenCount": 12345})
        assert result is not None
        assert result.startswith("foo ")
        assert result.endswith("12,345tok")
        assert width_of(result) == 80

    def test_usage_percentage_joined_with_slash(self) -> None:
        result = render({"id": "t1", "name": "foo", "tokenCount": 1500, "contextWindowSize": 200000})
        assert result is not None
        assert result.endswith("1,500tok/1%")

    @pytest.mark.parametrize("context_window_size", [0, -1, None, "invalid"])
    def test_usage_percentage_omitted_on_invalid_context_window(self, context_window_size: Any) -> None:
        result = render({"id": "t1", "name": "foo", "tokenCount": 1500, "contextWindowSize": context_window_size})
        assert result is not None
        assert result.endswith("1,500tok")

    def test_status_shown(self) -> None:
        result = render({"id": "t1", "name": "foo", "status": "running"})
        assert result is not None
        assert result.endswith("running")

    @pytest.mark.parametrize(
        ("start_time", "expected"),
        [
            (int((NOW - datetime.timedelta(seconds=45)).timestamp() * 1000), "45s"),
            ((NOW - datetime.timedelta(seconds=296)).isoformat(), "4m56s"),
            ((NOW - datetime.timedelta(seconds=4980)).isoformat().replace("+00:00", "Z"), "1h23m"),
        ],
    )
    def test_elapsed_formats(self, start_time: Any, expected: str) -> None:
        result = render({"id": "t1", "name": "foo", "startTime": start_time})
        assert result is not None
        assert result.endswith(expected)

    @pytest.mark.parametrize(
        "start_time",
        [True, "not-a-date", None, int((NOW + datetime.timedelta(seconds=60)).timestamp() * 1000)],
    )
    def test_elapsed_omitted_on_invalid_or_future_start(self, start_time: Any) -> None:
        assert render({"id": "t1", "name": "foo", "startTime": start_time}) == "foo"

    def test_full_combination(self) -> None:
        result = render(
            {
                "id": "t1",
                "name": "impl",
                "description": "実装作業中",
                "model": "claude-opus-4-8",
                "tokenCount": 1500,
                "contextWindowSize": 200000,
                "status": "running",
                "startTime": int((NOW - datetime.timedelta(seconds=45)).timestamp() * 1000),
            },
        )
        assert result is not None
        assert result.startswith(f"impl{SEP}Opus{SEP}実装作業中")
        assert result.endswith(f"45s{SEP}1,500tok/1%{SEP}running")
        assert width_of(result) == 80


class TestMain:
    """main関数の入出力全体を検証する。"""

    @pytest.mark.parametrize("raw", ["", "not json", "[]", "{}"])
    def test_invalid_or_empty_input_returns_zero(
        self, raw: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setattr("sys.stdin", io.StringIO(raw))
        assert claude_subagent_status_line.main() == 0
        assert capsys.readouterr().out == ""

    @pytest.mark.parametrize("columns", [None, "80", True, 0, -1])
    def test_invalid_columns_falls_back_to_default(
        self, columns: Any, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        payload = json.dumps({"columns": columns, "tasks": [{"id": "t1", "name": "foo", "description": "x" * 100}]})
        monkeypatch.setattr("sys.stdin", io.StringIO(payload))
        assert claude_subagent_status_line.main() == 0
        out_lines = capsys.readouterr().out.strip().splitlines()
        content = json.loads(out_lines[0])["content"]
        assert width_of(content) <= claude_subagent_status_line._DEFAULT_COLUMNS

    def test_valid_columns_used_as_width(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        payload = json.dumps({"columns": 10, "tasks": [{"id": "t1", "name": "foo", "description": "x" * 100}]})
        monkeypatch.setattr("sys.stdin", io.StringIO(payload))
        assert claude_subagent_status_line.main() == 0
        out_lines = capsys.readouterr().out.strip().splitlines()
        content = json.loads(out_lines[0])["content"]
        assert width_of(content) <= 10

    def test_emits_one_json_line_per_task(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        payload = json.dumps(
            {
                "columns": 80,
                "tasks": [
                    {"id": "t1", "name": "foo", "model": "claude-sonnet-5"},
                    {"name": "no-id"},
                    "not-a-dict",
                ],
            }
        )
        monkeypatch.setattr("sys.stdin", io.StringIO(payload))
        assert claude_subagent_status_line.main() == 0
        out_lines = capsys.readouterr().out.strip().splitlines()
        assert len(out_lines) == 1
        assert json.loads(out_lines[0]) == {"id": "t1", "content": f"foo{SEP}Sonnet"}

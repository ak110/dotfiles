"""scripts/claude_subagent_status_line.py のテスト。"""

import datetime
import io
import json
import unicodedata
from typing import Any

import claude_subagent_status_line
import pytest

SEP = " · "
NOW = datetime.datetime(2026, 1, 1, 0, 0, 0, tzinfo=datetime.UTC)


def render(task: dict[str, Any], width: int = 80, name_width: int | None = None) -> str | None:
    return claude_subagent_status_line.render_task(task, width, NOW, name_width)


def width_of(text: str) -> int:
    """表示幅をテスト側で独立に算出する（実装の`_display_width`を参照しない既知値計算）。"""
    return sum(2 if unicodedata.east_asian_width(char) in "WFA" else 1 for char in text)


class TestRenderTaskLeft:
    """render_task関数の左寄せグループ組み立てを`render`（公開関数）経由で観点別に検証する。"""

    def test_missing_id_returns_none(self) -> None:
        assert render({"name": "foo"}) is None

    @pytest.mark.parametrize("invalid_id", [None, 42, "", []])
    def test_invalid_id_returns_none(self, invalid_id: Any) -> None:
        assert render({"id": invalid_id, "name": "foo"}) is None

    def test_name_only(self) -> None:
        assert render({"id": "t1", "name": "foo"}) == "foo"

    @pytest.mark.parametrize(
        ("task", "expected"),
        [
            ({"id": "t1", "label": "foo"}, "foo"),
            ({"id": "t1", "type": "local_agent"}, "local_agent"),
            ({"id": "t1", "name": "foo", "label": "bar"}, "foo"),
            ({"id": "t1", "label": "bar", "type": "local_agent"}, "bar"),
        ],
    )
    def test_name_fallback_order_name_then_label_then_type(self, task: dict[str, Any], expected: str) -> None:
        assert render(task) == expected

    def test_model_only_without_name(self) -> None:
        assert render({"id": "t1", "model": "claude-sonnet-5"}) == "(Sonnet)"

    def test_name_and_model_both_missing_renders_empty(self) -> None:
        assert render({"id": "t1"}) == ""

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
        assert render({"id": "t1", "name": "foo", "model": model_id}) == f"foo ({expected})"

    def test_description_included(self) -> None:
        assert render({"id": "t1", "name": "foo", "description": "bar"}) == "foo  bar"

    def test_empty_description_omitted(self) -> None:
        assert render({"id": "t1", "name": "foo", "description": "   "}) == "foo"

    def test_description_newlines_collapsed_to_spaces(self) -> None:
        result = render({"id": "t1", "name": "foo", "description": "l1\nl2\r\nl3\rl4   l5"}, 200)
        assert result == "foo  l1 l2 l3 l4 l5"

    def test_description_truncated_to_fit_width(self) -> None:
        result = render({"id": "t1", "name": "foo", "description": "x" * 100}, 10)
        assert result is not None
        assert width_of(result) <= 10
        assert result.endswith("…")

    def test_description_exact_fit_not_truncated(self) -> None:
        result = render({"id": "t1", "name": "foo", "description": "x" * 4}, 9)
        assert result == "foo  xxxx"

    def test_description_budget_zero_dropped(self) -> None:
        result = render({"id": "t1", "name": "ab", "description": "bar", "status": "sssss"}, 10)
        assert result == "ab   sssss"

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


class TestNameColumnWidth:
    """名前列の幅揃え・上限切り詰め（`columns // 3`セル上限）を`render`経由で検証する。"""

    def test_name_width_padding_stripped_when_no_trailing_segment(self) -> None:
        """説明・右寄せグループが無い場合、共通名前幅のパディングは行末で除去される。"""
        result = render({"id": "t1", "name": "ab"}, width=80, name_width=5)
        assert result == "ab"

    def test_gap_between_name_column_and_description_is_at_least_two(self) -> None:
        result = render({"id": "t1", "name": "ab", "description": "x"}, width=80, name_width=5)
        assert result == "ab     x"

    def test_zero_width_name_column_omits_name_and_leading_gap(self) -> None:
        """名前列上限が0セルとなる極小`columns`では名前列を省略し、説明の前に余分な区切りを挿入しない。"""
        result = render({"id": "t1", "description": "bar"}, width=2)
        assert result is not None
        assert not result.startswith(" ")

    def test_name_truncated_but_model_preserved(self) -> None:
        """名前列が上限を超える場合、モデル名を保持したまま名前部分のみ省略記号で切り詰める。"""
        result = render({"id": "t1", "name": "x" * 20, "model": "claude-sonnet-5"}, width=80, name_width=13)
        assert result == "xx… (Sonnet)"

    def test_model_alone_exceeds_cap_falls_back_to_whole_column_truncation(self) -> None:
        """モデル名だけで上限を超える極端な場合は名前列全体を省略記号付きで切り詰める。"""
        result = render({"id": "t1", "name": "ab", "model": "claude-opus-4-8"}, width=80, name_width=5)
        assert result is not None
        assert width_of(result) <= 5
        assert result.endswith("…")

    def test_fullwidth_name_truncated_with_ellipsis(self) -> None:
        """全角文字を含む名前列も表示幅基準で切り詰められる。"""
        result = render({"id": "t1", "name": "あいうえお"}, width=80, name_width=4)
        assert result == "あ…"

    def test_name_column_with_only_ellipsis_fitting(self) -> None:
        """名前列の残余幅が省略記号のみ収まる幅の場合、省略記号のみを表示する。"""
        result = render({"id": "t1", "name": "x" * 10}, width=80, name_width=2)
        assert result == "…"


class TestRenderTaskRight:
    """render_task関数の右寄せグループと幅充填を観点別に検証する。"""

    def test_tokens_in_k_unit_right_aligned(self) -> None:
        result = render({"id": "t1", "name": "foo", "tokenCount": 176183})
        assert result is not None
        assert result.startswith("foo ")
        assert result.endswith("176.2k")
        assert width_of(result) == 80

    def test_usage_percentage_joined_with_slash(self) -> None:
        result = render({"id": "t1", "name": "foo", "tokenCount": 1500, "contextWindowSize": 200000})
        assert result is not None
        assert result.endswith("1.5k/1%")

    @pytest.mark.parametrize("context_window_size", [0, -1, None, "invalid"])
    def test_usage_percentage_omitted_on_invalid_context_window(self, context_window_size: Any) -> None:
        result = render({"id": "t1", "name": "foo", "tokenCount": 1500, "contextWindowSize": context_window_size})
        assert result is not None
        assert result.endswith("1.5k")

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
        assert result.startswith("impl (Opus)  実装作業中")
        assert result.endswith(f"45s{SEP}1.5k/1%{SEP}running")
        assert width_of(result) == 80

    def test_right_group_exceeding_columns_capped_to_line_width(self) -> None:
        """右寄せグループ単体が`columns`を超える極小`columns`では最終行を`columns`以内へ切り詰める。"""
        result = render({"id": "t1", "name": "a", "status": "x" * 50}, width=10)
        assert result is not None
        assert width_of(result) <= 10
        assert result.endswith("…")


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
        assert width_of(content) <= 80  # 既定幅（実装側_DEFAULT_COLUMNSの既知値）

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
        assert json.loads(out_lines[0]) == {"id": "t1", "content": "foo (Sonnet)"}

    def test_name_column_aligned_across_tasks_in_same_payload(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        payload = json.dumps(
            {
                "columns": 80,
                "tasks": [
                    {"id": "t1", "name": "ab", "description": "d1"},
                    {"id": "t2", "name": "reviewer-x", "description": "d2"},
                ],
            }
        )
        monkeypatch.setattr("sys.stdin", io.StringIO(payload))
        assert claude_subagent_status_line.main() == 0
        out_lines = capsys.readouterr().out.strip().splitlines()
        contents = {json.loads(line)["id"]: json.loads(line)["content"] for line in out_lines}
        desc_start_t1 = contents["t1"].index("d1")
        desc_start_t2 = contents["t2"].index("d2")
        assert desc_start_t1 == desc_start_t2

    def test_description_alignment_kept_when_name_missing_on_some_tasks(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """名前欄が空のタスクが混在しても、共通名前列幅の空白列を確保し説明開始位置を揃える。"""
        payload = json.dumps(
            {
                "columns": 80,
                "tasks": [
                    {"id": "t1", "name": "reviewer-x", "description": "d1"},
                    {"id": "t2", "description": "d2"},
                ],
            }
        )
        monkeypatch.setattr("sys.stdin", io.StringIO(payload))
        assert claude_subagent_status_line.main() == 0
        out_lines = capsys.readouterr().out.strip().splitlines()
        contents = {json.loads(line)["id"]: json.loads(line)["content"] for line in out_lines}
        assert contents["t2"].startswith(" ")
        assert contents["t1"].index("d1") == contents["t2"].index("d2")

    def test_label_used_as_name_when_name_field_absent(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        payload = json.dumps({"columns": 80, "tasks": [{"id": "t1", "label": "再レビュー docs"}]})
        monkeypatch.setattr("sys.stdin", io.StringIO(payload))
        assert claude_subagent_status_line.main() == 0
        out_lines = capsys.readouterr().out.strip().splitlines()
        assert json.loads(out_lines[0])["content"] == "再レビュー docs"

#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Claude Code subagentStatusLine: サブエージェント行にモデル短縮名・使用率・経過時間等を表示する。

stdinから公式subagentStatusLine JSON入力（`columns`・`tasks`配列）を受け取る。
タスクごとに左寄せグループ`name · 短縮モデル名 · description`と
右寄せグループ`経過時間 · トークン数/使用率% · status`を組み立て、
右寄せグループが`columns`幅の右端へ揃うよう空白を充填した1行を
`{"id": <task id>, "content": <行>}`のJSON行として標準出力へ出力する。
`model`未提供タスクはモデル部を省略し、使用率はtokenCount/contextWindowSizeから算出する
（いずれか欠落・非数値・contextWindowSizeが0以下の場合は省略）。
経過時間は`startTime`（エポックミリ秒またはISO 8601）から算出する。
`description`は改行を空白へ置換して1行化し、連続空白を1個へ畳んでから
表示幅（East Asian Width基準、全角2セル換算）で残り幅へ切り詰める。
`id`欠落タスクは出力対象外とする（Claude Code v2.1.205以降が`model`・`contextWindowSize`を提供する。
effortフィールド（v2.1.214以降）は未使用）。
"""

import datetime
import json
import re
import sys
import unicodedata
from typing import Any

_SEP = " · "
_GAP_MIN = 2
_DEFAULT_COLUMNS = 80
_MODEL_SHORT_PATTERNS: tuple[tuple[str, str], ...] = (
    ("opus", "Opus"),
    ("sonnet", "Sonnet"),
    ("haiku", "Haiku"),
    ("fable", "Fable"),
)


def main() -> int:
    """stdinから入力JSONを読み、タスクごとの上書き行をJSON行で標準出力へ出力する。"""
    raw = sys.stdin.read()
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return 0
    if not isinstance(data, dict):
        return 0
    columns = data.get("columns")
    width = columns if isinstance(columns, int) and not isinstance(columns, bool) and columns > 0 else _DEFAULT_COLUMNS
    tasks = data.get("tasks")
    if not isinstance(tasks, list):
        return 0
    now = datetime.datetime.now(datetime.UTC)
    for task in tasks:
        if not isinstance(task, dict):
            continue
        content = render_task(task, width, now)
        if content is None:
            continue
        sys.stdout.write(json.dumps({"id": task.get("id"), "content": content}, ensure_ascii=False) + "\n")
    return 0


def render_task(task: dict[str, Any], width: int, now: datetime.datetime) -> str | None:
    """タスク1件を1行分の`content`文字列へレンダリングする。`id`欠落時はNoneを返す。"""
    task_id = task.get("id")
    if not isinstance(task_id, str) or not task_id:
        return None

    name = task.get("name")
    name = name if isinstance(name, str) else ""
    description = task.get("description")
    description = _normalize_description(description) if isinstance(description, str) else ""
    model = task.get("model")
    model_label = _short_model_name(model) if isinstance(model, str) and model else None

    left_parts = [part for part in (name, model_label) if part]
    right = _SEP.join(_build_right_parts(task, now))

    reserved = _display_width(_SEP.join(left_parts))
    if description:
        reserved += _display_width(_SEP)
    if right:
        reserved += _GAP_MIN + _display_width(right)
    desc_budget = max(width - reserved, 0)
    desc = _truncate(description, desc_budget) if description else ""

    left = _SEP.join(left_parts + ([desc] if desc else []))
    if not right:
        return left
    gap = max(width - _display_width(left) - _display_width(right), _GAP_MIN)
    return left + " " * gap + right


def _build_right_parts(task: dict[str, Any], now: datetime.datetime) -> list[str]:
    """経過時間・トークン数/使用率・statusの右寄せセグメント一覧を組み立てる。"""
    parts: list[str] = []
    elapsed = _format_elapsed(task.get("startTime"), now)
    if elapsed is not None:
        parts.append(elapsed)
    tokens = _format_tokens(task.get("tokenCount"), task.get("contextWindowSize"))
    if tokens is not None:
        parts.append(tokens)
    status = task.get("status")
    if isinstance(status, str) and status:
        parts.append(status)
    return parts


def _format_tokens(token_count: Any, context_window_size: Any) -> str | None:
    """トークン数と使用率%を`1,500tok/1%`形式へ整形する。使用率算出不能時はトークン数のみ。"""
    count = _as_number(token_count)
    if count is None:
        return None
    text = f"{int(count):,}tok"
    pct = _context_usage_pct(token_count, context_window_size)
    if pct is not None:
        text += f"/{pct:.0f}%"
    return text


def _format_elapsed(start_time: Any, now: datetime.datetime) -> str | None:
    """`startTime`からの経過時間を`1h23m`・`4m56s`・`45s`形式へ整形する。解釈不能・未来時刻はNone。"""
    start = _parse_start_time(start_time)
    if start is None:
        return None
    seconds = int((now - start).total_seconds())
    if seconds < 0:
        return None
    if seconds >= 3600:
        return f"{seconds // 3600}h{seconds % 3600 // 60}m"
    if seconds >= 60:
        return f"{seconds // 60}m{seconds % 60}s"
    return f"{seconds}s"


def _parse_start_time(value: Any) -> datetime.datetime | None:
    """エポックミリ秒数値またはISO 8601文字列をaware datetimeへ変換する。解釈不能はNone。"""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.datetime.fromtimestamp(value / 1000, tz=datetime.UTC)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str):
        try:
            parsed = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=datetime.UTC)
        return parsed
    return None


def _normalize_description(text: str) -> str:
    """`description`内の改行を空白へ置換し、連続する空白を1個へ畳んで1行化する。"""
    return re.sub(r"\s+", " ", text).strip()


def _short_model_name(model_id: str) -> str:
    """モデルIDをファミリー名の短縮表示へ変換する。未知IDはそのまま返す。"""
    lowered = model_id.lower()
    for pattern, label in _MODEL_SHORT_PATTERNS:
        if pattern in lowered:
            return label
    return model_id


def _context_usage_pct(token_count: Any, context_window_size: Any) -> float | None:
    """トークン数とコンテキストウィンドウサイズから使用率%を算出する。欠落・非数値・0以下でNoneを返す。"""
    count = _as_number(token_count)
    window = _as_number(context_window_size)
    if count is None or window is None or window <= 0:
        return None
    return count / window * 100


def _as_number(value: Any) -> float | None:
    """値を数値へ変換する。bool・非数値・欠落でNoneを返す。"""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _display_width(text: str) -> int:
    """East Asian Width基準の表示幅を返す。`W`・`F`の文字は2セル、他は1セル換算とする。"""
    return sum(2 if unicodedata.east_asian_width(char) in "WF" else 1 for char in text)


def _truncate(text: str, budget: int) -> str:
    """文字列を表示幅`budget`セル以内へ省略記号付きで切り詰める。"""
    if budget <= 0:
        return ""
    if _display_width(text) <= budget:
        return text
    if budget == 1:
        return "…"
    chars: list[str] = []
    used = 0
    for char in text:
        char_width = _display_width(char)
        if used + char_width > budget - 1:
            break
        chars.append(char)
        used += char_width
    return "".join(chars) + "…"


if __name__ == "__main__":
    sys.exit(main())

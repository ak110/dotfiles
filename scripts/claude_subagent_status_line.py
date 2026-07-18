#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Claude Code subagentStatusLine: サブエージェント行に名前・モデル短縮名・使用率等を表示する。

stdinから公式subagentStatusLine JSON入力（`columns`・`tasks`配列）を受け取る。
タスクごとに名前列`{名前} ({短縮モデル名})`と`description`を左側に、
`経過時間 · トークン数(k単位)/使用率% · status`の右寄せグループを右側に配置した1行を
`{"id": <task id>, "content": <行>}`のJSON行として標準出力へ出力する。

名前列は同一入力内の全タスクを走査して表示幅を揃え（`columns // 3`セル上限）、
上限超過時はモデル名を保持したまま名前部分のみ省略記号付きで切り詰める
（モデル名だけで上限を超える極端な場合は名前列全体を切り詰める）。
名前の由来は`name`→`label`→`type`の順で非空文字列を採用する
（実ペイロードは`name`フィールドを持たず`label`に実質同一の値が入る。Claude Code 2.1.214時点で実測確認済み）。
`model`未提供タスクは括弧書き省略、名前・モデル双方欠落時は名前列が空文字列になる。
名前列・説明・右寄せグループは実在するセグメントの組合せだけに区切り幅を適用し、行末尾の空白は除去する。
使用率はtokenCount/contextWindowSizeから算出する（いずれか欠落・非数値・contextWindowSizeが0以下の場合は省略）。
経過時間は`startTime`（エポックミリ秒またはISO 8601）から算出する。
`description`は改行を空白へ置換して1行化し、連続空白を1個へ畳んでから
表示幅（East Asian WidthのW/F/A、曖昧幅を含め全角2セル換算）で残り幅へ切り詰める。
`id`欠落タスクは出力対象外とする。最終行は端末幅`columns`セル以内へ収める。

`name`指定＋`run_in_background=true`起動のnamed subagent（teammate）はタスク種別
`in_process_teammate`として管理され、本スクリプトの適用対象`local_agent`型から構造的に除外される
（Claude Code 2.1.214実測確認。ドキュメント未記載の制約であり本スクリプト側では対処しない）。
"""

import datetime
import json
import re
import sys
import unicodedata
from typing import Any

_SEP = " · "
_ELLIPSIS = "…"
_GAP_MIN = 2
_DEFAULT_COLUMNS = 80
_NAME_WIDTH_DIVISOR = 3
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
    name_width = _compute_name_width(tasks, width)
    for task in tasks:
        if not isinstance(task, dict):
            continue
        content = render_task(task, width, now, name_width)
        if content is None:
            continue
        sys.stdout.write(json.dumps({"id": task.get("id"), "content": content}, ensure_ascii=False) + "\n")
    return 0


def _compute_name_width(tasks: list[Any], width: int) -> int:
    """同一入力内の有効タスク全件から名前列の共通表示幅を算出する（`columns // 3`セル上限）。"""
    cap = max(width // _NAME_WIDTH_DIVISOR, 0)
    widths = [
        min(_display_width(_name_column(task)), cap)
        for task in tasks
        if isinstance(task, dict) and isinstance(task.get("id"), str) and task.get("id")
    ]
    return max(widths, default=0)


def render_task(task: dict[str, Any], width: int, now: datetime.datetime, name_width: int | None = None) -> str | None:
    """タスク1件を1行分の`content`文字列へレンダリングする。`id`欠落時はNoneを返す。

    `name_width`省略時は当該タスク単独の名前列幅（`columns // 3`セル上限）を用いる。
    実在するセグメント（名前列・説明・右寄せグループ）の組合せだけに区切り幅を適用し、
    最終行は末尾空白を除去したうえで表示幅`width`セル以内へ収める。
    """
    task_id = task.get("id")
    if not isinstance(task_id, str) or not task_id:
        return None

    cap = max(width // _NAME_WIDTH_DIVISOR, 0)
    col_width = name_width if name_width is not None else min(_display_width(_name_column(task)), cap)
    name_present = col_width > 0
    padded_name = ""
    if name_present:
        name_col = _fit_name_column(task, col_width)
        padded_name = name_col + " " * max(col_width - _display_width(name_col), 0)

    description = task.get("description")
    description = _normalize_description(description) if isinstance(description, str) else ""
    right = _SEP.join(_build_right_parts(task, now))
    right_present = bool(right)

    reserved = _display_width(padded_name) if name_present else 0
    if name_present and description:
        reserved += _GAP_MIN
    if right_present:
        reserved += _GAP_MIN + _display_width(right)
    desc_budget = max(width - reserved, 0)
    desc = _truncate(description, desc_budget) if description else ""

    left_parts = [part for part in ((padded_name if name_present else ""), desc) if part]
    left = (" " * _GAP_MIN).join(left_parts)

    if right_present:
        gap = max(width - _display_width(left) - _display_width(right), _GAP_MIN if left else 0)
        line = left + " " * gap + right
    else:
        line = left

    line = line.rstrip()
    return _truncate(line, width) if _display_width(line) > width else line


def _name_column(task: dict[str, Any]) -> str:
    """名前列の未切り詰め文字列`{名前} ({短縮モデル名})`を組み立てる（表示幅算出専用）。

    双方欠落時は空文字列を返す。実際の描画には`_fit_name_column`を使う。
    """
    name = _name_label(task)
    model = task.get("model")
    model_label = _short_model_name(model) if isinstance(model, str) and model else None
    if name and model_label:
        return f"{name} ({model_label})"
    if name:
        return name
    if model_label:
        return f"({model_label})"
    return ""


def _fit_name_column(task: dict[str, Any], width_cap: int) -> str:
    """名前列を`width_cap`セル以内で組み立てる。モデル名を保持したまま名前部分のみ切り詰める。

    モデル名だけで`width_cap`を超える極端な場合は名前列全体を省略記号付きで切り詰める。
    """
    if width_cap <= 0:
        return ""
    name = _name_label(task)
    model = task.get("model")
    model_label = _short_model_name(model) if isinstance(model, str) and model else None

    if name and model_label:
        suffix = f" ({model_label})"
        full = f"{name}{suffix}"
        if _display_width(full) <= width_cap:
            return full
        name_budget = width_cap - _display_width(suffix)
        if name_budget <= 0:
            return _truncate(full, width_cap)
        return _truncate(name, name_budget) + suffix
    if name:
        return _truncate(name, width_cap)
    if model_label:
        return _truncate(f"({model_label})", width_cap)
    return ""


def _name_label(task: dict[str, Any]) -> str:
    """名前の由来を`name`→`label`→`type`の順で非空文字列を採用する。全欠落時は空文字列を返す。"""
    for key in ("name", "label", "type"):
        value = task.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


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
    """トークン数と使用率%を`176.1k/18%`形式へ整形する。使用率算出不能時はトークン数のみ。"""
    count = _as_number(token_count)
    if count is None:
        return None
    text = f"{count / 1000:.1f}k"
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
    """East Asian Width基準の表示幅を返す。`W`・`F`・`A`（曖昧幅を含む）の文字は2セル、他は1セル換算とする。"""
    return sum(2 if unicodedata.east_asian_width(char) in "WFA" else 1 for char in text)


def _truncate(text: str, budget: int) -> str:
    """文字列を表示幅`budget`セル以内へ省略記号付きで切り詰める。

    省略記号`…`（U+2026）はEast Asian Widthが`A`（曖昧幅）のため`_display_width`基準で2セルを占める。
    ハードコードした1セル前提は表示幅超過を招くため、`_display_width(_ELLIPSIS)`で実測した幅を予約する。
    """
    if budget <= 0:
        return ""
    if _display_width(text) <= budget:
        return text
    ellipsis_width = _display_width(_ELLIPSIS)
    if budget < ellipsis_width:
        return ""
    chars: list[str] = []
    used = 0
    for char in text:
        char_width = _display_width(char)
        if used + char_width > budget - ellipsis_width:
            break
        chars.append(char)
        used += char_width
    return "".join(chars) + _ELLIPSIS


if __name__ == "__main__":
    sys.exit(main())

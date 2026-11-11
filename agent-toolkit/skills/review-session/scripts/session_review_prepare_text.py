"""振り返りの入力準備（session_review_prepare_text）。"""

from __future__ import annotations

import json
import pathlib
import re
from typing import Any

from agent_toolkit._common.json_lines import split_lines

_SUMMARY_TOP_COUNT = 10


_SUMMARY_LENGTH = 200
"""候補の概要へ載せる文字数。通読の本文と操作入力の全文は別に保持する。"""


def _read_jsonl(path: pathlib.Path) -> list[dict[str, Any]]:
    return _parse_jsonl(path.read_text(encoding="utf-8"), str(path))


def _parse_jsonl(text: str, source: str) -> list[dict[str, Any]]:
    """ensure_ascii=FalseとLFで生成したJSONLを、物理行の位置を保って読む。"""
    events = []
    for number, line in enumerate(split_lines(text), 1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
            if not isinstance(event, dict):
                raise ValueError("JSON objectではない")
        except ValueError as error:
            raise ValueError(f"{source}:{number}（物理行）: {error}") from error
        events.append(event)
    return events


def _fence(body: str, info: str = "text") -> list[str]:
    """本文中のバッククォート連続より長いフェンスで囲み、本文中の見出しを見出しとして扱わせない。"""
    longest = max((len(run) for run in re.findall(r"`+", body)), default=0)
    fence = "`" * max(3, longest + 1)
    return [f"{fence}{info}", body, fence]


def _one_line(text: str, limit: int = _SUMMARY_LENGTH) -> str:
    """空白を正規化した先頭の文字列を返す。"""
    normalized = " ".join(text.split())
    return normalized if len(normalized) <= limit else normalized[:limit] + "…"


def _clip_utf8(text: str, limit: int) -> str:
    """UTF-8の文字境界を保ち、本文を指定バイト数以内へ縮める。"""
    encoded = text.encode("utf-8")
    if len(encoded) <= limit:
        return text
    marker = "…"
    body = encoded[: max(0, limit - len(marker.encode("utf-8")))].decode("utf-8", errors="ignore")
    return body + marker


def _full_body_lines(text: str, *, title: str) -> list[str]:
    """長い単一行も上限内で読み、連続する文字位置から全文を復元できる形で返す。"""
    lines = []
    for start in range(0, max(1, len(text)), 4000):
        body = text[start : start + 4000]
        lines.extend(["", f"## {title}（文字{start + 1}〜{start + len(body)}）", "", *_fence(body)])
    return lines


def _comparison_document(events: list[dict[str, Any]], detail_command: str) -> str:
    """正常な操作を比較する事実と全文位置を示し、量の多寡と欠陥の判定を分ける。"""
    lines = [
        "",
        "## 委譲入力・規範読込の比較材料",
        "",
        "各記録の行を記録別に並べる。記録をまたいだ時系列ではない。",
        "explicit-readは明示された対象、opaque-commandは命令文字列だけの観測であり、読了した資料は推定しない。"
        "入力文字数は観測した直接本文だけ、結果文字数は記録の返却本文の量である。"
        "ファイル参照の本文量、自動配送、現在のファイルサイズ、token量を取得量へ代用しない。",
    ]
    previous_record: str | None = None
    for event in events:
        if event["record"] != previous_record:
            lines.extend(["", f"### {event['record']}（{event['role']}）", ""])
            previous_record = event["record"]
        locator = f"{event['record']}:{event['line']}"
        result = f"{event['record']}:{event['result_line']}" if event.get("result_line") is not None else "未確認"
        body_count = event.get("observed_body_characters")
        result_count = event.get("recorded_result_characters")
        persisted = f"退避あり、退避前={event.get('original_utf8_bytes', '未確認')}バイト、" if event.get("persisted") else ""
        lines.append(
            f"- {event['category']} {event['tool']}、担当={event['record']}（{event['role']}）、"
            f"時点={event.get('timestamp') or '未確認'}、形式={event['input_form']}、"
            f"直接本文={body_count if body_count is not None else '未確認'}字、"
            f"記録の結果本文={result_count if result_count is not None else '未確認'}字、"
            f"{persisted}"
            f"対象={json.dumps(event['target'], ensure_ascii=False)}、入力={locator}、結果={result}、"
            f"代表入力={_one_line(str(event.get('summary', '')))}"
        )
        lines.extend(_full_body_lines(str(event.get("summary", "")), title=f"入力 {locator}"))
    lines.extend(["", f"全文と参照先は`{detail_command} --detail <入力または結果の記録位置>`から取得する。", ""])
    return "\n".join(lines)

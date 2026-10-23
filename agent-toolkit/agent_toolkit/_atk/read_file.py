"""UTF-8本文を、位置情報と本文を分けた表示予算内で取得する。"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import shlex
import sys

from agent_toolkit._atk import outcome
from agent_toolkit._common.read_triggers import ReadTarget, resolve_read_source

MAX_RESPONSE_BYTES = 16 * 1024
"""返却全体が通常出力の自動保存の対象にならない最大の予算。"""
RESPONSE_BYTES = MAX_RESPONSE_BYTES
_PLUGIN_ROOT = pathlib.Path(__file__).resolve().parents[2]


def first_read_command(path: pathlib.Path) -> str:
    """保存先の表示とhookの案内が共に示す、最初の読取コマンドを返す。"""
    return f"atk read-file -- {shlex.quote(str(path))}"


def build_parser(parser: argparse.ArgumentParser) -> None:
    """本文読取の位置と対象を登録する。"""
    parser.add_argument(
        "--section",
        default="",
        help="Markdownの見出し名。複数節は「節1」「節2」、範囲は「開始」から「終了」まで、冒頭も指定できる。位置は選択本文の文字位置とする。",
    )
    parser.add_argument("--start", type=int, default=0, help="取得を始める0以上の文字位置。省略時は0。")
    parser.add_argument(
        "--max-bytes",
        type=int,
        default=RESPONSE_BYTES,
        help=f"メタデータ、区切りのLFと本文を含む返却全体のUTF-8バイト予算。省略時は{RESPONSE_BYTES}、最大{MAX_RESPONSE_BYTES}。",
    )
    parser.add_argument("path", type=pathlib.Path, help="UTF-8ファイルの絶対パス。")


def _response(text: str, start: int, end: int, section: str = "") -> str:
    eof = end == len(text)
    fields: dict[str, object] = {"start": start, "end": end, "next": None if eof else end, "eof": eof}
    if section:
        fields["section"] = section
    metadata = json.dumps(
        fields,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return f"{metadata}\n{text[start:end]}"


def dispatch(args: argparse.Namespace) -> int:
    """対象の本文を読み、返却予算に収まる連続部分を出力する。"""
    try:
        if not args.path.is_absolute():
            raise ValueError(f"絶対パスではありません: {args.path}")
        if args.start < 0:
            raise ValueError(f"開始位置が負です: {args.start}")
        claude = os.environ.get("CLAUDE_CODE_ENTRYPOINT") or os.environ.get("CLAUDECODE")
        host = "codex" if os.environ.get("CODEX_THREAD_ID") and not claude else "claude"
        target = resolve_read_source(args.path, _PLUGIN_ROOT, host)
        with target.open(encoding="utf-8", newline="") as stream:
            text = stream.read()
        section = getattr(args, "section", "")
        if section:
            text = ReadTarget(target, section).body()
        start = min(args.start, len(text))
    except (OSError, UnicodeError, ValueError) as error:
        outcome.report_failure(str(error), next_action="実在するUTF-8ファイルの絶対パスと、0以上の--startを指定する")
        return 2
    minimum_end = min(len(text), start + 1)
    minimum_bytes = len(_response(text, start, minimum_end, section).encode("utf-8"))
    if not minimum_bytes <= args.max_bytes <= MAX_RESPONSE_BYTES:
        outcome.report_failure(
            f"返却予算{args.max_bytes}バイトでは取得できません（必要な最小予算{minimum_bytes}、最大予算{MAX_RESPONSE_BYTES}）",
            next_action=f"--max-bytesに{minimum_bytes}以上{MAX_RESPONSE_BYTES}以下を指定し、内側と外側の出力予算にも収める",
        )
        return 2
    low = start
    high = min(len(text), low + args.max_bytes)
    while low < high:
        middle = (low + high + 1) // 2
        if len(_response(text, start, middle, section).encode("utf-8")) <= args.max_bytes:
            low = middle
        else:
            high = middle - 1
    sys.stdout.buffer.write(_response(text, start, low, section).encode("utf-8"))
    return 0

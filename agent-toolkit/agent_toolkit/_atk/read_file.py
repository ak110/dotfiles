"""UTF-8本文を、位置情報を含むJSON全体の表示予算内で取得する。"""

from __future__ import annotations

import argparse
import json
import pathlib
import shlex
import sys

from agent_toolkit._atk import outcome

RESPONSE_BYTES = 4096


def first_read_command(path: pathlib.Path) -> str:
    """保存表示とhookが共用する最初の読取コマンド。"""
    return f"atk read-file -- {shlex.quote(str(path))}"


def build_parser(parser: argparse.ArgumentParser) -> None:
    """本文読取の位置と対象を登録する。"""
    parser.add_argument("--start", type=int, default=0, help="取得を始める0以上の文字位置。省略時は0。")
    parser.add_argument("path", type=pathlib.Path, help="UTF-8ファイルの絶対パス。")


def _response(text: str, start: int, end: int) -> str:
    eof = end == len(text)
    return (
        json.dumps(
            {"text": text[start:end], "start": start, "end": end, "next": None if eof else end, "eof": eof},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        + "\n"
    )


def dispatch(args: argparse.Namespace) -> int:
    """対象の本文を読み、返却予算に収まる連続部分を出力する。"""
    try:
        if not args.path.is_absolute():
            raise ValueError(f"絶対パスではありません: {args.path}")
        if args.start < 0:
            raise ValueError(f"開始位置が負です: {args.start}")
        with args.path.open(encoding="utf-8", newline="") as stream:
            text = stream.read()
        if args.start > len(text):
            raise ValueError(f"開始位置{args.start}が本文の末尾{len(text)}を超えています")
    except (OSError, UnicodeError, ValueError) as error:
        outcome.report_failure(str(error), next_action="実在するUTF-8ファイルの絶対パスと、本文内の0以上の--startを指定する")
        return 2
    low = args.start
    high = min(len(text), low + RESPONSE_BYTES)
    while low < high:
        middle = (low + high + 1) // 2
        if len(_response(text, args.start, middle).encode("utf-8")) <= RESPONSE_BYTES:
            low = middle
        else:
            high = middle - 1
    sys.stdout.write(_response(text, args.start, low))
    return 0

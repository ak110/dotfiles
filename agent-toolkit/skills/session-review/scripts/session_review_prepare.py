#!/usr/bin/env -S uv run --no-project --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""セッション振り返りの証拠抽出に必要な準備項目を1行のJSONで取得する。

本スクリプトは検査スクリプトではなくデータ取得ツールであるため、
`agent-toolkit:agent-standards`の`references/check-script-design.md`が定める「成功時無出力」規定は適用せず、
引数誤用と準備項目を取得できない実行前エラーを終了コード2とする区分だけを踏襲する。
"""

from __future__ import annotations

import argparse
import datetime
import json
import pathlib
import shutil
import subprocess
import sys
from typing import Any


def _build_parser() -> argparse.ArgumentParser:
    """コマンドライン引数を定義する。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--transcript", metavar="PATH", help="Claude Codeのtranscriptパス。")
    parser.add_argument("--codex-thread-id", metavar="ID", help="Codexのthread ID。")
    parser.add_argument("--target-repo", metavar="PATH", help="未処理項目を取得する対象リポジトリ。")
    return parser


def _missing(item: str) -> int:
    """取得できなかった準備項目を報告する。"""
    print(f"不足: {item}", file=sys.stderr)
    return 2


def _run_atk(executable: str, arguments: list[str]) -> subprocess.CompletedProcess[str] | None:
    """`atk`を実行し、起動できない場合は`None`を返す。"""
    try:
        return subprocess.run(
            [executable, *arguments],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except OSError:
        return None


def _managed_temp_path(result: subprocess.CompletedProcess[str] | None) -> pathlib.Path | None:
    """管理対象一時領域の作成結果から検証済みの絶対パスを返す。"""
    if result is None or result.returncode != 0:
        return None
    lines = result.stdout.splitlines()
    if len(lines) != 1:
        return None
    path = pathlib.Path(lines[0])
    return path if path.is_absolute() and path.is_dir() else None


def _pending_items(result: subprocess.CompletedProcess[str] | None) -> list[dict[str, str]] | None:
    """一覧取得結果をファイル名と要約の配列として検証する。"""
    if result is None or result.returncode != 0:
        return None
    items: list[dict[str, str]] = []
    try:
        for line in result.stdout.splitlines():
            value: Any = json.loads(line)
            if (
                not isinstance(value, dict)
                or set(value) != {"filename", "summary"}
                or not isinstance(value["filename"], str)
                or not isinstance(value["summary"], str)
            ):
                return None
            items.append({"filename": value["filename"], "summary": value["summary"]})
    except json.JSONDecodeError:
        return None
    return items


def _cleanup(executable: str, path: pathlib.Path) -> None:
    """準備失敗後の管理対象一時領域を回収する。"""
    _run_atk(executable, ["managed-temp", "cleanup", "--path", str(path)])


def main(argv: list[str] | None = None, *, now: datetime.datetime | None = None) -> int:
    """準備項目を取得して1行のJSONを出力する。"""
    args = _build_parser().parse_args(argv)
    if (args.transcript is None) == (args.codex_thread_id is None):
        return _missing("transcript_path")

    evidence_script = pathlib.Path(__file__).resolve().with_name("session_review_evidence.py")
    if not evidence_script.is_file():
        return _missing("evidence_script")

    transcript_path = pathlib.Path(args.transcript).expanduser().resolve() if args.transcript is not None else None
    if transcript_path is not None and not transcript_path.is_file():
        return _missing("transcript_path")

    executable = shutil.which("atk")
    if executable is None:
        return _missing("managed_temp")
    create_result = _run_atk(executable, ["managed-temp", "create", "--prefix", "session-review"])
    managed_temp = _managed_temp_path(create_result)
    if managed_temp is None:
        return _missing("managed_temp")

    current = now if now is not None else datetime.datetime.now(datetime.UTC)
    observation_boundary = current.astimezone(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    target_repo = pathlib.Path(args.target_repo).expanduser().resolve() if args.target_repo is not None else None
    pending_items = None
    if target_repo is not None:
        list_result = _run_atk(
            executable,
            [
                "wi",
                "list",
                f"--target-repo={target_repo}",
                "--status=active",
                "--summary-only",
                "--skip-pull",
            ],
        )
        pending_items = _pending_items(list_result)
        if pending_items is None:
            _cleanup(executable, managed_temp)
            return _missing("pending_items")

    record = {
        "evidence_script": str(evidence_script),
        "transcript_path": str(transcript_path) if transcript_path is not None else None,
        "codex_thread_id": args.codex_thread_id,
        "managed_temp": str(managed_temp),
        "observation_boundary": observation_boundary,
        "target_repo": str(target_repo) if target_repo is not None else None,
        "pending_items": pending_items,
    }
    print(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main())

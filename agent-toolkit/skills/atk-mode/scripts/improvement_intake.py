"""観測の受付とメインの処置結果を保存する。正式WIは生成しない。"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

from agent_toolkit._common.atomic_file import atomic_write
from agent_toolkit._common.state_paths import state_dir
from filelock import FileLock, Timeout

_REQUIRED = {"target_repo", "intent", "observation", "impact"}
_OPTIONAL = {"evidence", "proposal", "origin"}
_WI = re.compile(r"\d{8}-\d{6}-\d{3}\.md")
_MAX_BYTES = 16_384


class _IntakeError(ValueError):
    """診断に外部入力を含めない受付エラー。"""


def _read_report(path: Path) -> dict[str, str]:
    with path.open("rb") as stream:
        raw = stream.read(_MAX_BYTES + 1)
    if len(raw) > _MAX_BYTES:
        raise _IntakeError("受付入力が16 KiBを超える。必要な観測だけを残す。")
    try:
        report = json.loads(raw.decode("utf-8"))
    except ValueError, RecursionError:
        raise _IntakeError("原稿の形式が不正。UTF-8のJSON原稿に直して受付を再実行する。") from None
    if (
        not isinstance(report, dict)
        or not _REQUIRED <= report.keys() <= _REQUIRED | _OPTIONAL
        or any(not isinstance(value, str) or not value.strip() for value in report.values())
    ):
        raise _IntakeError("対象リポジトリ、利用意図、観測、影響を空でない文字列で指定する。")
    return {key: value.strip() for key, value in report.items()}


def _load(path: Path) -> dict[str, dict[str, Any]]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except ValueError, RecursionError:
        raise _IntakeError("保存済みの受付記録の形式が不正。原本を保全し、正常な版から復元する。") from None
    if not isinstance(value, dict) or any(not isinstance(row, dict) for row in value.values()):
        raise _IntakeError("受付記録の書式を確認できない。原本を保護して復元する。")
    return value


def _change(args: argparse.Namespace, records: dict[str, dict[str, Any]]) -> object:
    if args.operation == "capture":
        report = _read_report(args.body_file)
        encoded = json.dumps(report, ensure_ascii=False, sort_keys=True).encode("utf-8")
        identifier = hashlib.sha256(encoded).hexdigest()
        records.setdefault(identifier, {"report": report})
        return {"id": identifier, **records[identifier]}
    ids = tuple(dict.fromkeys(args.id))
    if any(identifier not in records for identifier in ids):
        raise _IntakeError("受付IDを特定できない。listで候補を確かめる。")
    if len({records[identifier]["report"]["target_repo"] for identifier in ids}) != 1:
        raise _IntakeError("異なるリポジトリの候補を同じ処置に含められない。")
    if args.status in {"covered", "registered"}:
        if args.wi is None or _WI.fullmatch(args.wi) is None:
            raise _IntakeError("既存または登録済みの正式WIのファイル名を指定する。")
    elif args.wi is not None:
        raise _IntakeError("この処置には正式WIを指定しない。")
    decision = {"status": args.status, "reason": args.reason.strip(), "wi": args.wi}
    if not decision["reason"]:
        raise _IntakeError("判断根拠を指定する。")
    for identifier in ids:
        previous = records[identifier].get("decision")
        if previous and previous["wi"] is not None and previous["wi"] != args.wi:
            raise _IntakeError("別の正式WIへ対応付け済みの候補がある。元の処置を確認する。")
    for identifier in ids:
        records[identifier]["decision"] = decision
    return {"ids": ids, **decision}


def main(argv: list[str] | None = None) -> int:
    """受付結果と処置をJSONで返す。失敗時は記録の成功を返さない。"""
    parser = argparse.ArgumentParser(description=__doc__)
    operations = parser.add_subparsers(dest="operation", required=True, help="保存・照会・処置から操作を選ぶ")
    capture = operations.add_parser("capture", help="観測を保存する")
    capture.add_argument("--body-file", required=True, type=Path, metavar="PATH", help="観測原稿のJSONファイル")
    listing = operations.add_parser("list", help="観測と処置を照会する")
    listing.add_argument("--target-repo", required=True, help="照会するリポジトリの識別子")
    decide = operations.add_parser("decide", help="メインが確定した処置を保存する")
    decide.add_argument("--id", action="append", required=True, help="処置する受付ID（反復可）")
    decide.add_argument(
        "--status", required=True, choices=("waiting", "discarded", "covered", "registered"), help="確定した処置"
    )
    decide.add_argument("--reason", required=True, help="処置を選んだ根拠")
    decide.add_argument("--wi", help="対応する正式WIのファイル名")
    args = parser.parse_args(argv)
    path = state_dir() / "improvement-intake.json"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with FileLock(path.with_suffix(".lock"), timeout=10):
            records = _load(path)
            if args.operation == "list":
                result = [
                    {"id": identifier, **row}
                    for identifier, row in records.items()
                    if row["report"]["target_repo"] == args.target_repo
                ]
            else:
                result = _change(args, records)
                atomic_write(path, json.dumps(records, ensure_ascii=False), fsync=True)
    except _IntakeError as error:
        print(f"受付または処置を完了できない: {error}", file=sys.stderr)
        return 2
    except Timeout:
        print(f"受付の保存先が使用中。即時通知は継続し、解放後に {args.operation} を同じ引数で実行する。", file=sys.stderr)
        return 2
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f"受付または処置を完了できない: {type(error).__name__}。入力と状態ディレクトリを確かめる。", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

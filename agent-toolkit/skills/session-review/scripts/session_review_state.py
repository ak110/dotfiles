"""非同期振り返りの待ち状態を照会し、準備済み範囲を成功確定する。"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys

from agent_toolkit._common import session_review


def main(argv: list[str] | None = None) -> int:
    """show/completeの公開入力を受理し、結果を1行JSONで返す。"""
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True, help="待ちの照会、範囲の成功確定、batch分析完了の記録。")
    commands.add_parser("show")
    commands.add_parser("finish", help="batch分析の完了を記録する。範囲確定は起動元が報告同期を検収して行う。")
    complete = commands.add_parser("complete")
    complete.add_argument("--prepared", required=True, metavar="PATH", help="成功したprepareの1行JSONを保存したファイル。")
    args = parser.parse_args(argv)
    try:
        if args.command == "show":
            targets = session_review.pending()
            result = {"pending_count": len(targets), "targets": targets}
        elif args.command == "complete":
            result = session_review.complete(session_review.read(pathlib.Path(args.prepared)))
        else:
            value = os.environ.get(session_review.BATCH_ENV)
            if not value:
                raise ValueError("finishはrun-skillが起動したbatch内で実行する")
            path = pathlib.Path(value)
            manifest = session_review.read(path)
            if not manifest.get("prepared") or any(item.get("unconfirmed_scope") for item in manifest["prepared"]):
                raise ValueError("未準備または未確認範囲のあるbatchは完了できない")
            manifest["analysis_succeeded"] = True
            session_review.write(path, manifest)
            result = {"run_id": manifest["run_id"], "analysis_succeeded": True}
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f"失敗: {error}\n次の操作: --preparedの準備結果と元記録を確認し、成功した準備結果で再実行する", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())

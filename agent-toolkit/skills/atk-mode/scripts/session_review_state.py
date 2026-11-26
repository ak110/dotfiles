"""非同期振り返りの待ち状態を照会し、準備済み範囲を成功確定する。"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import subprocess
import sys
import time

import filelock
from agent_toolkit._atk.wi import add_reports
from agent_toolkit._common import private_notes, session_review


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True, help="待ちの照会、範囲の成功確定、batch分析完了の記録。")
    commands.add_parser("show")
    commands.add_parser("finish", help="報告の保存を確かめ、batch分析の完了を記録する。範囲の確定は起動元が行う。")
    complete = commands.add_parser("complete")
    complete.add_argument("--prepared", required=True, metavar="PATH", help="成功したprepareの1行JSONを保存したファイル。")
    args = parser.parse_args(argv)
    try:
        if args.command == "show":
            targets = session_review.pending()
            result = {"pending_count": len(targets), "targets": targets}
        elif args.command == "complete":
            result = session_review.complete(session_review.read(pathlib.Path(args.prepared)))
            try:
                session_review.sweep_reviewed(now=time.time())
            except (OSError, ValueError, KeyError, TypeError) as error:
                print(
                    f"警告: 処理済み範囲の回収を保留: {error}。保存物の内容とアクセス権を確かめ、"
                    "原因を直して atk run-script session-review-state -- complete "
                    "--prepared <元の準備結果の絶対パス> を再実行する",
                    file=sys.stderr,
                )
        else:
            value = os.environ.get(session_review.BATCH_ENV)
            if not value:
                raise ValueError("finishはrun-skillが起動したbatch内で実行する")
            path = pathlib.Path(value)
            writer = session_review.BatchWriter(path)
            batch = writer.load()
            if batch.status in {session_review.BatchStatus.COMPLETED, session_review.BatchStatus.RESOLVED}:
                raise ValueError("確定済みbatchは更新できない")
            notes = pathlib.Path(batch.report_notes) if batch.report_notes else private_notes.default_private_notes()
            run_id = batch.run_id
            try:
                gap = add_reports.check_run_report_sync(notes, run_id=run_id, scope=session_review.BATCH_SCOPE, local_only=True)
            except (OSError, ValueError, RuntimeError, filelock.Timeout, subprocess.SubprocessError) as error:
                writer.record_report_step("analysis-finish", {"notes": str(notes), "status": type(error).__name__})
                raise ValueError("報告用UWIの保存を確認できない") from error
            writer.record_report_step(
                "analysis-finish", {"notes": str(notes), "status": "report-committed" if gap is None else gap.reason}
            )
            if gap is not None:
                raise ValueError(f"報告用UWIを確かめてからfinishを再実行する: {gap.reason}")
            try:
                manifest = writer.analyze()
            except ValueError:
                writer.record_report_step("analysis-finish", {"status": "analysis-incomplete"})
                raise
            result = {"run_id": manifest["run_id"], "analysis_succeeded": True}
    except (OSError, ValueError, KeyError, TypeError) as error:
        next_action = (
            "同じ実行の報告用UWIと分析対象の準備結果を確認し、batch内でfinishを再実行する"
            if args.command == "finish"
            else "--preparedの準備結果と元記録を確認し、成功した準備結果で再実行する"
        )
        print(f"失敗: {error}\n次の操作: {next_action}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""外部情報の調査で、前回読んだ位置より後の変更履歴とX投稿を取得し、1つのJSONで標準出力へ書く。  pyfltr: allow-comment

読めた位置は回の保留として保存し、回の結果のUWIを保存した後に起動元が確定する。
`--mark-read`は、全版を評価した固定の変更履歴と、読了したその他の`model_sources`を回の保留へ加える。
X APIは利用開始の設定（`weekly_research_x_api`）とキーが揃うまで呼ばない。
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import subprocess
import sys

from agent_toolkit._atk import config_migration, research_sources, research_subscriptions, weekly_schedule
from agent_toolkit._common.next_action import ActionableError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--slot",
        metavar="WORK@DATE",
        help="回の識別子（例: research@2026-11-22）。省略時は起動元が渡した回、無ければ最新の週枠の回。",
    )
    parser.add_argument(
        "--mark-read",
        nargs="+",
        metavar="SOURCE",
        help="全版を評価した変更履歴と読了したmodel_sourcesを回の保留へ加える（結果のUWIを保存する前に実行する）。",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--subscription-candidates",
        action="store_true",
        help="WIと規範で参照された発信者の全候補をX APIを呼ばずに集計する。",
    )
    group.add_argument("--candidate-year", type=int, metavar="YEAR", help="指定年のWIと規範から発信者の全候補を集計する。")
    for action, label in (("add", "追加"), ("remove", "削除")):
        for kind, subject in (("account", "Xアカウント"), ("query", "検索語")):
            parser.add_argument(
                f"--{action}-{kind}", action="append", metavar="VALUE", help=f"{subject}を共有購読へ{label}する。"
            )
    args = parser.parse_args(argv)
    add = {key: tuple(values) for key, values in (("accounts", args.add_account), ("queries", args.add_query)) if values}
    remove = {
        key: tuple(values) for key, values in (("accounts", args.remove_account), ("queries", args.remove_query)) if values
    }
    audit = args.subscription_candidates or args.candidate_year is not None
    if sum(map(bool, (audit, add or remove, args.mark_read))) > 1:
        parser.error("候補の集計、購読の変更、読了の記録は別々に実行する")
    try:
        result = _run(args, add, remove, audit=audit)
    except ActionableError as error:
        print(f"失敗: {error.message}", file=sys.stderr)
        return 1
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print(
            f"失敗: 調査の情報源を処理できない: {error}。次の操作: private-notesの配置・同期と一覧の内容を確かめて再実行する",
            file=sys.stderr,
        )
        return 1
    json.dump(result, sys.stdout, ensure_ascii=False, indent=1)
    sys.stdout.write("\n")
    return 0


def _run(
    args: argparse.Namespace,
    add: research_subscriptions.Subscriptions,
    remove: research_subscriptions.Subscriptions,
    *,
    audit: bool,
) -> dict[str, object]:
    if add or remove:
        config_migration.migrate_legacy_settings()
        return {"x_subscriptions": research_subscriptions.update(add=add, remove=remove)}
    if audit:
        _, candidates = research_sources.subscription_evidence(
            research_sources.repositories(), year=args.candidate_year, limit=None, include_subscribed=True
        )
        return {"subscription_candidates": candidates}
    slot = args.slot or os.environ.get(weekly_schedule.SLOT_ENV)
    if not slot:
        slot = weekly_schedule.slot_id("research", weekly_schedule.latest_slot(datetime.datetime.now(datetime.UTC)))
    work, _date = weekly_schedule.parse_slot_id(slot)
    if work != "research":
        raise ValueError(f"外部情報の調査の回ではない: {slot}")
    if args.mark_read:
        return {"slot": slot, "marked": research_sources.mark_read(slot, tuple(args.mark_read))}
    return research_sources.collect(slot)


if __name__ == "__main__":
    sys.exit(main())

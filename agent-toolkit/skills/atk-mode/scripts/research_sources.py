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
import sys

from agent_toolkit._atk import research_sources, weekly_schedule


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
    args = parser.parse_args(argv)
    slot = args.slot or os.environ.get(weekly_schedule.SLOT_ENV)
    if not slot:
        slot = weekly_schedule.slot_id("research", weekly_schedule.latest_slot(datetime.datetime.now(datetime.UTC)))
    try:
        work, _date = weekly_schedule.parse_slot_id(slot)
        if work != "research":
            raise ValueError(f"外部情報の調査の回ではない: {slot}")
        if args.mark_read:
            result: dict[str, object] = {"slot": slot, "marked": research_sources.mark_read(slot, tuple(args.mark_read))}
        else:
            result = research_sources.collect(slot)
    except (OSError, ValueError) as error:
        print(
            f"失敗: 情報源を取得できない: {error}。"
            "次の操作: 設定（`atk config show`）と状態ディレクトリの権限を確かめて再実行する",
            file=sys.stderr,
        )
        return 1
    json.dump(result, sys.stdout, ensure_ascii=False, indent=1)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

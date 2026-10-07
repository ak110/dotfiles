"""`atk wait-schedule`の引数の登録と実行。判定は`agent_toolkit._common.wait_schedule`が持つ。"""

import argparse
import json

from agent_toolkit._common import periodic_recheck as _periodic_recheck
from agent_toolkit._common import wait_schedule as _wait_schedule


def build_parser(parser: argparse.ArgumentParser) -> None:
    """`atk wait-schedule`の引数を登録する。"""
    parser.add_argument(
        "--request-bucket",
        choices=("main", "subagent"),
        required=True,
        help="判定対象のrequest bucket（mainまたはsubagent）。",
    )
    parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="出力形式。textはcron式の1行、jsonは`cron`と定期再確認の`prompt`を持つJSONを出力する（省略時はtext）。",
    )


def dispatch(args: argparse.Namespace) -> int:
    """Request bucketに対応するcron式（`--format json`では定期再確認のpromptも）を出力する。"""
    schedule = _wait_schedule.get_schedule(args.request_bucket)
    if args.format == "json":
        print(json.dumps({"cron": schedule, "prompt": _periodic_recheck.PERIODIC_RECHECK_PROMPT}, ensure_ascii=False))
    else:
        print(schedule)
    return 0

"""`atk wait-schedule`の引数の登録と実行。判定は`agent_toolkit._common.wait_schedule`が持つ。"""

import argparse
import json

from agent_toolkit._common import periodic_recheck as _periodic_recheck
from agent_toolkit._common import wait_schedule as _wait_schedule


def build_parser(parser: argparse.ArgumentParser) -> None:
    """`atk wait-schedule`は引数を取らない。"""
    del parser  # noqa


def dispatch(args: argparse.Namespace) -> int:
    """メインのcron式と定期再確認のpromptをJSONの1行で出力する。"""
    del args  # noqa
    schedule = _wait_schedule.get_schedule("main")
    print(json.dumps({"cron": schedule, "prompt": _periodic_recheck.PERIODIC_RECHECK_PROMPT}, ensure_ascii=False))
    return 0

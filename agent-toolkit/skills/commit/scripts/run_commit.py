"""POSIXのgit commitを有限実行し、中断時はhookの復元と所有子孫の回収を待つ。"""

from __future__ import annotations

import argparse
import contextlib
import math
import os
import signal
import subprocess
import sys
from collections.abc import Generator

import psutil
from pyfltr.command import execution_slot

from agent_toolkit._common import next_action, process_tree
from agent_toolkit._git import command as git_command


@contextlib.contextmanager
def _finish_interruption() -> Generator[None]:
    """有限の回収中に同じ中断を重ねず、呼出側のシグナル設定へ戻す。"""
    previous = {number: signal.signal(number, signal.SIG_IGN) for number in (signal.SIGINT, signal.SIGTERM)}
    try:
        yield
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)


def _interrupt(process: subprocess.Popen[bytes]) -> None:
    """親が先に終了しても、終了要求前に所有したhook・検証子を回収する。"""
    with _finish_interruption(), process_tree.ProcessIdentityPins() as pins:
        try:
            root = psutil.Process(process.pid)
            pins.retain(root)
            targets = [root, *process_tree.collect_descendants(process.pid, pins=pins)]
        except psutil.NoSuchProcess:
            process.wait(timeout=process_tree.RECLAIM_TIMEOUT_SECONDS)
            return
        # 別セッションの検証にも届くよう、グループだけでなく所有した各世代へ送る。
        # prekにはSIGINTで退避を戻す機会を与え、その終了も資源回収の対象に含める。
        for target in targets:
            with contextlib.suppress(psutil.NoSuchProcess):
                target.send_signal(signal.SIGINT)
        alive = process_tree.wait_until_released(targets, timeout=process_tree.RECLAIM_TIMEOUT_SECONDS)
        result = process_tree.stop_tree(None, known=alive, pins=pins)
        if residual := result.descriptions():
            raise process_tree.ReclaimError(residual, result.targets, context="git commit interruption")
        process.wait(timeout=process_tree.RECLAIM_TIMEOUT_SECONDS)


def _term_as_interrupt(_number: int, _frame: object) -> None:
    raise KeyboardInterrupt


def main(argv: list[str] | None = None) -> int:
    """公開commit-with-timeoutからGitの終了コード、上限124、中断130を返す。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout", required=True, type=float, help="commit全体の時間上限（正の有限秒数）")
    parser.add_argument("commit_args", nargs=argparse.REMAINDER, help="--の後にgit commitの引数を指定する")
    args = parser.parse_args(argv)
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error("--timeoutは正の有限秒数で指定する")
    if os.name != "posix":
        parser.error("この中断・退避復元の実行方法はPOSIX専用。Windowsでは確認済みのホスト実行手段を使う")
    arguments = args.commit_args[1:] if args.commit_args[:1] == ["--"] else args.commit_args
    previous = signal.signal(signal.SIGTERM, _term_as_interrupt)
    try:
        # 枠待ちは上限から除く。hookは祖先の所有を継承し、commit単位で保持する。
        with (
            execution_slot.hold(label="git commit"),
            process_tree.process_scope(
                git_command.command_line(["commit", *arguments]),
                start_new_session=True,
            ) as process,
        ):
            try:
                code = process.wait(timeout=args.timeout)
            except subprocess.TimeoutExpired:
                _interrupt(process)
                return 124
            except KeyboardInterrupt:
                _interrupt(process)
                return 130
            return code if code >= 0 else 128 - code
    except KeyboardInterrupt:
        return 130
    except (OSError, process_tree.ReclaimError) as error:
        next_action.report(
            str(error), next_action="表示された所有プロセスとhookの退避を確認し、回収・復元後に同じcommitを再実行する"
        )
        return 125
    finally:
        signal.signal(signal.SIGTERM, previous)


if __name__ == "__main__":
    sys.exit(main())

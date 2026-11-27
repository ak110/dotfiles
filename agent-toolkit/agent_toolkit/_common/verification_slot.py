from __future__ import annotations

import argparse
import contextlib
import os
import pathlib
import signal
import sys
import tempfile
import time
from collections.abc import Callable, Generator

from pyfltr import file_lock
from pyfltr.command import execution_slot

from agent_toolkit._common import process_tree
from agent_toolkit._common.inherited_venv import strip_inherited_venv

# pyfltr 3.34の既存の要求・排他・所有者の形式を使う。別の実行枠は作らない。  # pyfltr: allow-comment
# pylint: disable=protected-access


@contextlib.contextmanager
def hold(
    *,
    priority: str = "normal",
    label: str | None = None,
    should_abort: Callable[[], bool] | None = None,
    on_wait: Callable[[execution_slot.SlotOwner | None], None] | None = None,
    on_wait_interval: execution_slot.WaitIntervalCallback | None = None,
    lock_dir: pathlib.Path | None = None,
) -> Generator[None]:
    if priority not in {"normal", "integration", "urgent"}:
        raise ValueError(f"不正な検証の優先度: {priority}")
    if priority == "normal" or getattr(execution_slot._local, "depth", False):
        with execution_slot.hold(
            label=label, should_abort=should_abort, on_wait=on_wait, on_wait_interval=on_wait_interval, lock_dir=lock_dir
        ):
            yield
        return
    directory = lock_dir if lock_dir is not None else execution_slot.default_lock_dir()
    slots = [execution_slot._slot_paths(directory, index) for index in range(execution_slot.slot_count())]
    held: list[tuple[file_lock.FileLock, pathlib.Path]] = []

    def release() -> None:
        while held:
            execution_slot._release(*held.pop())

    def acquire() -> bool:
        with execution_slot._queue_guard(directory):
            execution_slot._waiters(directory)
            rank = 0 if priority == "urgent" else 1
            # 連番0は通常要求より先、同じ優先度は受付順。既存の多枠要求として読める名前を保つ。  # pyfltr: allow-comment
            with tempfile.NamedTemporaryFile(
                prefix=f"execution.wait-{0:020d}-m-{rank}-{time.monotonic_ns():020d}-", dir=directory, delete=False
            ) as stream:
                request = pathlib.Path(stream.name)
            request_lock = file_lock.FileLock(request)
            if not request_lock.try_acquire():
                request.unlink(missing_ok=True)
                raise OSError(f"検証の要求をロックできない: {request}")
        try:
            notified = False
            while True:
                owners = execution_slot.read_owners(directory)
                if any(execution_slot._is_ancestor(owner) for owner in owners):
                    return False
                if should_abort is not None and should_abort():
                    raise execution_slot.WaitAborted("検証の待機が取り消された")
                with execution_slot._queue_guard(directory):
                    waiters = execution_slot._waiters(directory)
                    for index in execution_slot._eligible_slots(waiters, request, len(slots)):
                        lock_path, owner_path = slots[index]
                        lock = file_lock.FileLock(lock_path)
                        if lock.try_acquire():
                            try:
                                execution_slot._write_owner(owner_path, label)
                            except BaseException:
                                lock.release()
                                raise
                            held.append((lock, owner_path))
                            return True
                if not notified:
                    notified = True
                    if on_wait is not None:
                        on_wait(owners[0] if owners else None)
                time.sleep(execution_slot._POLL_INTERVAL)
        finally:
            with execution_slot._queue_guard(directory):
                request_lock.release(remove=True)

    execution_slot._observe(on_wait_interval, acquire, release=release)
    execution_slot._local.depth = 1
    try:
        yield
    finally:
        execution_slot._local.depth = 0
        release()


def _interrupt(_number: int, _frame: object) -> None:
    raise KeyboardInterrupt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="既存の検証枠で緊急修正と統合検証を優先する")
    parser.add_argument(
        "--priority",
        required=True,
        choices=("normal", "integration", "urgent"),
        help="normalは通常、integrationは統合、urgentは緊急修正",
    )
    parser.add_argument("command", nargs=argparse.REMAINDER, help="--の後に検証コマンドと引数を指定する")
    args = parser.parse_args(argv)
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("--の後に検証コマンドを指定する")
    environment = dict(os.environ)
    strip_inherited_venv(environment)
    previous = signal.signal(signal.SIGTERM, _interrupt)
    try:
        with (
            hold(priority=args.priority, label=" ".join(command)),
            process_tree.process_scope(command, env=environment) as process,
        ):
            code = process.wait()
        return code if code >= 0 else 128 - code
    except KeyboardInterrupt:
        return 130
    except (OSError, process_tree.ReclaimError) as error:
        print(str(error), file=sys.stderr)
        return 125
    finally:
        signal.signal(signal.SIGTERM, previous)


if __name__ == "__main__":
    sys.exit(main())

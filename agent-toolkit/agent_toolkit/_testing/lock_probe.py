"""spawn子がbackendやテスト本体を読み込まずに行う、OS間共有ロックの照会。"""

import errno
import pathlib
from multiprocessing.connection import Connection

from agent_toolkit._common import file_lock


def probe_lock(path: str, connection: Connection) -> None:
    """起動準備、ロック可否、照会中の例外を別々のメッセージで返す。"""
    with connection:
        connection.send(("ready", None))
        try:
            with pathlib.Path(path).open("a+", encoding="utf-8") as handle:
                try:
                    file_lock.acquire_lock(handle, blocking=False)
                except OSError as error:
                    if error.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                        raise
                    available = False
                else:
                    file_lock.release_lock(handle)
                    available = True
            connection.send(("result", available))
        except OSError as error:
            connection.send(("error", f"{type(error).__name__}: {error}"))

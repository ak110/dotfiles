"""保存されたPID世代の実行プロセスを、再開前に子孫ごと停止する。"""

import logging
import os

import psutil

from agent_toolkit._common import process_tree, session_registry

_LOG = logging.getLogger(__name__)


def stop_recorded_executor(identity: session_registry.ProcessIdentity) -> str | None:
    """保存されたPIDと起動時刻が一致する実行プロセスを停止し、確認できない場合は理由を返す。

    PIDの再利用は元の実行が終了した状態として扱う。
    他の会話を所有するサーバーのプロセスは呼び出し側で除外する。
    """
    if identity.pid == os.getpid():
        return f"pid {identity.pid} は呼び出し元自身のプロセスである"
    try:
        process = psutil.Process(identity.pid)
        created = process.create_time()
    except psutil.NoSuchProcess:
        return None
    except psutil.Error as error:
        return f"pid {identity.pid} の起動時刻を照会できない: {error}"
    if created != identity.create_time:
        return None
    # 列挙後に生成される子孫も回収するため、生成の停止と終了確認を共通処理へ委ねる。
    result = process_tree.stop_tree(process)
    if identity.exists():
        return f"pid {identity.pid} の終了を確認できない: residual={result.descriptions()}"
    process_tree.log_residual(result.descriptions(), context="recorded executor")
    _LOG.info("recorded_executor_stopped pid=%d create_time=%s", identity.pid, identity.create_time)
    return None

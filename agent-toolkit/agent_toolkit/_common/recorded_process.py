"""保存されたPID世代の実行プロセスを、再開前に子孫ごと停止する。"""

import logging
import os

import psutil

from agent_toolkit._common import process_tree, reclaim_records, session_registry

_LOG = logging.getLogger(__name__)


def stop_recorded_executor(identity: session_registry.ProcessIdentity) -> str | None:
    """保存されたPIDと起動時刻が一致する実行プロセスを子孫ごと停止し、回収を確認できない場合は理由を返す。

    起点の終了だけでは停止済みとしない。子孫が残る場合と生成の停止を確認できない場合も理由を返し、
    所有した対象を`reclaim_records`へ残す。同じ識別の再呼び出しは、起点が消滅・再利用されていても
    残した対象を回収し直し、全て回収できたら記録を消して`None`を返す。
    PIDの再利用先は元の実行とみなさず、シグナルを送らない。
    他の会話を所有するサーバーのプロセスは呼び出し側で除外する。
    """
    if identity.pid == os.getpid():
        return f"pid {identity.pid} は呼び出し元自身のプロセスである"
    try:
        record = reclaim_records.load(identity.pid, identity.create_time)
    except ValueError as error:
        return f"pid {identity.pid} の回収待ちを確認できない: {error}"
    root: psutil.Process | None
    try:
        root = psutil.Process(identity.pid)
        if root.create_time() != identity.create_time:
            root = None
    except psutil.NoSuchProcess:
        root = None
    except psutil.Error as error:
        return f"pid {identity.pid} の起動時刻を照会できない: {error}"
    known = record.live_targets() if record is not None else []
    if root is None and not known:
        reclaim_records.delete(identity.pid, identity.create_time)
        return None
    # 列挙後に生成される子孫も回収するため、生成の停止と終了確認を共通処理へ委ねる。
    result = process_tree.stop_tree(root, known=known)
    if root is not None and identity.exists():
        reclaim_records.save(identity.pid, identity.create_time, result.targets, result.unresolved)
        return f"pid {identity.pid} の終了を確認できない: residual={result.descriptions()}"
    if result.residual or result.unresolved:
        reclaim_records.save(identity.pid, identity.create_time, result.targets, result.unresolved)
        return (
            f"pid {identity.pid} は終了したが子孫の回収を確認できない: residual={result.descriptions()}。"
            "同じ停止を再試行して残った対象を回収する"
        )
    reclaim_records.delete(identity.pid, identity.create_time)
    _LOG.info("recorded_executor_stopped pid=%d create_time=%s", identity.pid, identity.create_time)
    return None

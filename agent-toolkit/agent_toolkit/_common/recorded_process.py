"""保存されたPID世代の実行プロセスを、再開前に子孫ごと停止する。"""

import contextlib
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
    生成の停止を確認できなかった理由は再試行で解消せず、記録を残して理由を返し続ける。
    回収を確認できなかった直接の子は、zombie表示でも同じPID世代が残る間は回収待ちとして理由を返す。
    一時停止や列挙をすり抜けた子孫は親の終了後に起点から辿れず、残した対象の再停止では回収を確かめられないためである。
    運用者が残存を確かめて記録を削除するまで、呼び出し元は再開・終了の公開を止める。
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
        if not identity.matches(root):
            if identity.exists():
                return f"pid {identity.pid} の旧形式の識別を確定できないため停止しない。対象のPIDと起動コマンドを確認する"
            root = None
    except psutil.NoSuchProcess:
        root = None
    except psutil.Error as error:
        return f"pid {identity.pid} の起動時刻を照会できない: {error}"
    if record is not None and (
        len(record.live_targets(conservative=True)) != len(record.live_targets())
        or len(record.held_unreaped(conservative=True)) != len(record.held_unreaped())
    ):
        return f"pid {identity.pid} の旧形式の回収対象を識別できない。記録されたPIDと起動コマンドを確認する"
    known = record.live_targets() if record is not None else []
    unresolved = list(record.unresolved) if record is not None else []
    unreaped = record.held_unreaped() if record is not None else []
    if root is None and not known and not unreaped:
        if unresolved:
            return _unconfirmed_reason(identity, [], unresolved)
        reclaim_records.delete(identity.pid, identity.create_time)
        return None
    # 列挙後に生成される子孫も回収するため、生成の停止と終了確認を共通処理へ委ねる。
    result = process_tree.stop_tree(root, known=known)
    unresolved.extend(reason for reason in result.unresolved if reason not in unresolved)
    unreaped = process_tree.reclaim_unreaped(unreaped)
    residual = [result.describe_residual(process) for process in result.residual]
    residual.extend(f"{process.pid}: 回収を確認できない直接の子" for process in unreaped)
    if root is not None and identity.exists():
        reclaim_records.save(identity.pid, identity.create_time, result.targets, unresolved, unreaped)
        return f"pid {identity.pid} の終了を確認できない: residual={residual + _unresolved_descriptions(unresolved)}"
    if unresolved:
        reclaim_records.save(identity.pid, identity.create_time, result.targets, unresolved, unreaped)
        return _unconfirmed_reason(identity, residual, unresolved)
    if residual:
        reclaim_records.save(identity.pid, identity.create_time, result.targets, unresolved, unreaped)
        return (
            f"pid {identity.pid} は終了したが子孫の回収を確認できない: residual={residual}。"
            "同じ停止を再試行して残った対象を回収する"
        )
    reclaim_records.delete(identity.pid, identity.create_time)
    _LOG.info("recorded_executor_stopped pid=%d create_time=%s", identity.pid, identity.create_time)
    return None


def retain_unreclaimed(identity: session_registry.ProcessIdentity, failure: process_tree.ReclaimError) -> None:
    """起点の子を直接停止した呼び出し元が、回収できなかった所有対象を同じ識別の次の停止へ引き継ぐ。

    起点が終了した後は子孫を起点から辿れないため、`stop_recorded_executor`の再試行が使う記録へ所有対象を残す。
    回収を確認できなかった直接の子（`failure.child`）は、zombie表示でも終了済みと扱わないよう別に残す。
    """
    unreaped: list[psutil.Process] = []
    if failure.child is not None:
        with contextlib.suppress(psutil.NoSuchProcess):
            unreaped.append(psutil.Process(failure.child.pid))
    reclaim_records.save(identity.pid, identity.create_time, failure.processes, failure.unresolved, unreaped)


def _unresolved_descriptions(unresolved: list[str]) -> list[str]:
    return [f"生成の停止を確認できない: {reason}" for reason in unresolved]


def _unconfirmed_reason(identity: session_registry.ProcessIdentity, residual: list[str], unresolved: list[str]) -> str:
    """生成の停止を確認できなかった記録について、再試行では解消しない理由と解除の操作を返す。"""
    record = reclaim_records.path(identity.pid, identity.create_time)
    return (
        f"pid {identity.pid} の子孫の回収を確認できない: residual={residual + _unresolved_descriptions(unresolved)}。"
        "生成の停止を確認できなかったため、起点から辿れない子孫が残っている可能性があり、停止の再試行では回収を確かめられない。"
        "次の操作: `ps -o pid,ppid,lstart,args -e`などで、この実行の開始以降に起動した不要なプロセスを探して終了させ、"
        f"残っていないことを確かめてから回収待ちの記録{record}を削除し、同じ操作を再実行する"
    )

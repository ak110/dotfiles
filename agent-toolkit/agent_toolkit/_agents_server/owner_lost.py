"""所有サーバーを失った実行中のsessionの診断と委譲先CLIの停止、所有者のいない他ルートからの通知・終端結果の回収。

所有サーバーを失った実行中とは、生存の印が有効な状態ファイルがsessionを載せておらず、登録簿の記録プロセスのうち
委譲先CLIが実在する状態である。判定は`session_registry.running_ownership`へ集約し、本モジュールはその結果を
照会の応答・案内と停止の操作へ写す。

通知と終端結果は、委譲先の起動時のルート（所有サーバーのルート）へ届く。Codexを親とするMCPサーバーは起動単位ごとに
別のルートへ結び付くため、委譲元のサーバーが替わると届いた先を新しいサーバーが読まなくなる。
登録簿から解決したsessionに限り、生存の印が有効な書込主体のいない他のルートからも同じsessionの通知と結果を回収する。
"""

from __future__ import annotations

import dataclasses
import json
import logging
import os
import pathlib
from typing import Any

import psutil

from agent_toolkit._agents_server import notice_inbox, retained_results, shared_layout, status_file, status_reader
from agent_toolkit._common import process_tree, session_registry

_LOG = logging.getLogger("agent-toolkit.agents-server.mcp")

OWNER_LOST_NEXT_ACTION = (
    "所有サーバーを失った実行中のため、次のどちらかを選ぶ。"
    "作業を続けさせる場合は`atk agents wait`で実行の終了を待つ。"
    "続行の指示が必要な場合は`kill`で委譲先CLIを止めてから同じ`session_id`へ`send_message`する（実行中のturnは中断される）"
)
"""所有サーバーを失った実行中のsessionへの照会と追送が返す次の操作。待つ操作を先に示し、停止は呼出側の明示操作に限る。"""


@dataclasses.dataclass(frozen=True)
class OwnerLost:
    """所有サーバーを失った実行中のsessionの記録プロセス、記録時のルートと、そのルートに残る通知の件数。"""

    session_id: str
    resolution: session_registry.SessionResolution
    stranded_notice_count: int

    def diagnostics(self) -> dict[str, Any]:
        """照会の応答へ加える診断項目を返す。"""
        resolution = self.resolution
        processes: list[dict[str, Any]] = []
        if resolution.owner_process is not None:
            processes.append(
                {
                    "role": "owner_server",
                    **session_registry.process_payload(resolution.owner_process),
                    "alive": resolution.owner_process.exists(),
                }
            )
        processes.extend(
            {
                "role": "executor",
                **session_registry.process_payload(process),
                "alive": process in resolution.live_executors,
            }
            for process in resolution.executor_processes
        )
        return {
            "ownership": session_registry.RunningOwnership.OWNER_LOST.value,
            "recorded_processes": processes,
            "owner_root_session_id": resolution.owner_root_session_id,
            "stranded_notice_count": self.stranded_notice_count,
        }

    def message(self) -> str:
        """エラー応答の本文を返す。診断項目は1行のJSONで添える。"""
        return (
            f"error.recovery=owner_lost: {self.session_id}; the owning agents_server no longer publishes this session, "
            "and the delegate CLI running the turn is still alive; "
            + json.dumps(self.diagnostics(), ensure_ascii=False, separators=(",", ":"))
        )


def diagnose(
    session_id: str,
    resolution: session_registry.SessionResolution,
    *,
    own_root_session_id: str | None,
    state_root: pathlib.Path | None = None,
) -> OwnerLost | None:
    """`resolution`が所有サーバーを失った実行中を示す場合に診断を返し、それ以外は`None`を返す。"""
    if resolution.state is not session_registry.Resolution.RUNNING or not resolution.live_executors:
        return None
    ownership = session_registry.running_ownership(
        resolution, writer_alive=status_file.live_writer_holds_session(session_id, state_root)
    )
    if ownership is not session_registry.RunningOwnership.OWNER_LOST:
        return None
    return OwnerLost(session_id, resolution, count_stranded_notices(session_id, own_root_session_id, state_root))


def count_stranded_notices(session_id: str, own_root_session_id: str | None, state_root: pathlib.Path | None = None) -> int:
    """所有者のいない他のルートに残る、同じsessionの上り通知の件数を返す。"""
    count = 0
    for root_session_id in status_reader.ownerless_root_session_ids(own_root_session_id, state_root):
        directory = shared_layout.notices_directory(root_session_id, state_root)
        try:
            count += sum(1 for path in directory.iterdir() if path.suffix == ".json" and path.name.startswith(f"{session_id}."))
        except FileNotFoundError:
            continue
    return count


def take_stranded_notices(
    session_id: str,
    own_root_session_id: str | None,
    state_root: pathlib.Path | None = None,
    *,
    stash_directory: pathlib.Path | None = None,
    roots: list[str] | None = None,
) -> list[dict[str, str]]:
    """所有者のいない他のルートから同じsessionの上り通知を回収し、既存の通知と同じ公開形で返す。回収した原本は削除する。

    `roots`は呼び出し側が同じ巡回で列挙済みの所有者のいないルートで、省略時は自ら列挙する。
    """
    notices: list[dict[str, str]] = []
    if roots is None:
        roots = status_reader.ownerless_root_session_ids(own_root_session_id, state_root)
    for root_session_id in roots:
        # 退避先はルートごとに分ける。`take_notices`は退避先の全通知を毎回返すため、共有すると同じ通知を重ねて返す。
        stash = None if stash_directory is None else stash_directory / root_session_id
        notices.extend(notice_inbox.take_notices(root_session_id, session_id, state_root, stash_directory=stash))
    return notices


def has_stranded_result(session_id: str, own_root_session_id: str | None, state_root: pathlib.Path | None = None) -> bool:
    """所有者のいない他のルートに、同じsessionの未回収の終端結果が残るかを返す。"""
    return any(
        retained_results.read_retained_result(root_session_id, session_id, state_root) is not None
        for root_session_id in status_reader.ownerless_root_session_ids(own_root_session_id, state_root)
    )


def take_stranded_result(
    session_id: str,
    own_root_session_id: str | None,
    *,
    collector: str,
    state_root: pathlib.Path | None = None,
    stash_path: pathlib.Path | None = None,
    roots: list[str] | None = None,
) -> tuple[dict[str, Any] | None, str | None]:
    """所有者のいない他のルートに残る同じsessionの終端結果を、記録された所有者として一度だけ回収する。"""
    if roots is None:
        roots = status_reader.ownerless_root_session_ids(own_root_session_id, state_root)
    for root_session_id in roots:
        payload = retained_results.read_retained_result(root_session_id, session_id, state_root)
        if payload is None:
            continue
        owner = payload.get("owner_status_file")
        result, error = retained_results.take_result(
            root_session_id,
            session_id,
            owner if isinstance(owner, str) and owner else "root.json",
            collector=collector,
            state_root=state_root,
            stash_path=stash_path,
        )
        if result is not None or error is not None:
            return result, error
    return None, None


def stop_recorded_executor(identity: session_registry.ProcessIdentity) -> str | None:
    """登録簿のpidと起動時刻が一致する委譲先CLIを子孫ごと停止し、停止を確かめられない場合はその理由を返す。

    起動時刻が一致しないpidは別のプロセスへ再利用されたものであり、記録した委譲先CLIは終了済みとして停止しない。
    自プロセスは対象にしない。所有サーバーのプロセスは他のsessionの資源を持ち得るため、呼び出し側が渡さない。
    """
    if identity.pid == os.getpid():
        return f"pid {identity.pid} はこのMCPサーバー自身のプロセスである"
    try:
        process = psutil.Process(identity.pid)
        created = process.create_time()
    except psutil.NoSuchProcess:
        return None
    except psutil.Error as error:
        return f"pid {identity.pid} の起動時刻を照会できない: {error}"
    if created != identity.create_time:
        return None
    # 委譲先CLIの終了後は子孫の親が変わり辿れなくなるため、終了を要求する前に列挙する。
    targets = [process, *process_tree.collect_descendants(identity.pid)]
    residual = process_tree.reclaim_processes(targets)
    if identity.exists():
        return f"pid {identity.pid} の終了を確認できない: residual={[process_tree.describe(item) for item in residual]}"
    if residual:
        process_tree.log_residual([process_tree.describe(item) for item in residual], context="owner-lost executor")
    _LOG.info("owner_lost_executor_stopped pid=%d create_time=%s", identity.pid, identity.create_time)
    return None

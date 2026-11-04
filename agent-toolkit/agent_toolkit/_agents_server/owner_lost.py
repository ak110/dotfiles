"""所有サーバーを失った実行中のsessionの診断と委譲先CLIの停止、所有者のいない他ルートからの通知・終了時の結果の回収。

所有サーバーを失った実行中とは、生存の印が有効な状態ファイルがsessionを載せておらず、登録簿の記録プロセスのうち
委譲先CLIが実在する状態である。判定は`session_registry.running_ownership`へ集約し、本モジュールはその結果を
照会の応答・案内と停止の操作へ写す。

通知と終了時の結果は、委譲先の起動時のルート（所有サーバーのルート）へ届く。Codexを親とするMCPサーバーは起動単位ごとに
別のルートへ結び付くため、委譲元のサーバーが替わると届いた先を新しいサーバーが読まなくなる。
通知は現在の受取主体を確認し、生存の印が有効な書込主体のいない他のルートからも回収する。
終了時の結果は登録簿から解決したsessionについて、同じ旧ルートから回収する。
"""

from __future__ import annotations

import dataclasses
import json
import pathlib
from typing import Any

from agent_toolkit._agents_server import notice_inbox, retained_results, shared_layout, status_file, status_reader
from agent_toolkit._common import session_registry

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
    for root_session_id in _result_roots(session_id, own_root_session_id, state_root):
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
    recipient_session_id: str | None,
    stash_directory: pathlib.Path | None = None,
    roots: list[str] | None = None,
) -> list[dict[str, str]]:
    """所有者のいない他のルートから同じsessionの上り通知を回収し、既存の通知と同じ公開形で返す。回収した原本は削除する。

    `roots`は呼び出し側が同じ巡回で列挙済みの所有者のいないルートで、省略時は自ら列挙する。
    """
    notices: list[dict[str, str]] = []
    roots = _result_roots(session_id, own_root_session_id, state_root, roots=roots)
    for root_session_id in roots:
        # 退避先はルートごとに分ける。`take_notices`は退避先の全通知を毎回返すため、共有すると同じ通知を重ねて返す。
        stash = None if stash_directory is None else stash_directory / root_session_id
        owner = status_reader.load_root(root_session_id, state_root).sessions.get(session_id, {})
        owner_file = owner.get("owner_status_file") if owner.get("host_session_id") == recipient_session_id else None
        notices.extend(
            notice_inbox.take_notices(
                root_session_id,
                session_id,
                state_root,
                recipient_session_id=recipient_session_id,
                owner_status_file=owner_file,
                stash_directory=stash,
            )
        )
    return notices


def has_stranded_result(session_id: str, own_root_session_id: str | None, state_root: pathlib.Path | None = None) -> bool:
    """所有者のいない他のルートに、同じsessionの未回収の終了時の結果が残るかを返す。"""
    return any(
        retained_results.read_retained_result(root_session_id, session_id, state_root) is not None
        for root_session_id in _result_roots(session_id, own_root_session_id, state_root)
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
    """所有者のいない他のルートに残る同じsessionの終了時の結果を、記録された所有者として一度だけ回収する。"""
    roots = _result_roots(session_id, own_root_session_id, state_root, roots=roots)
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


def _result_roots(
    session_id: str,
    own_root_session_id: str | None,
    state_root: pathlib.Path | None,
    *,
    roots: list[str] | None = None,
) -> list[str]:
    """同じsessionの登録先を優先し、旧形式では所有者のいないルートも調べる。"""
    recorded = session_registry.resolve(session_id, state_root=state_root).owner_root_session_id
    candidates = roots if roots is not None else status_reader.ownerless_root_session_ids(own_root_session_id, state_root)
    return list(dict.fromkeys(root for root in (recorded, *candidates) if root is not None and root != own_root_session_id))

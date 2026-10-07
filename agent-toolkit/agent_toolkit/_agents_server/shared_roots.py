"""共有状態のルートsessionと書込主体の識別、および現行のsession識別子とホストsessionからルートへの索引（alias）。

Claude backendの委譲先は所有sessionと自身のClaude Code sessionを持つ。
この対応はClaude Code 2.1.261およびclaude-agent-sdk 0.2系で確認した。
Codex backendの委譲先自身のシェルは、所有sessionと自身のCodex threadを持つ。
2026年9月7日にCodex CLI 0.153.4の`start_shell`で起動したシェルに
`CODEX_THREAD_ID`が存在することを確認した。

Codex CLIが直接起動するMCPサーバープロセスへは、Codex App Server自身の環境が
継承されない。Codex CLI 0.153.4で2026年9月9日に確認した。`thread/start`の
`config.mcp_servers.agents_server`へ完全な定義を渡す場合だけ、`env`の識別子が
そのプロセスへ届く。ホスト、CLIまたはSDKを更新した時点では、同じ起動形で
MCPサーバーの環境変数と起動通知を確認する。
"""

from __future__ import annotations

import dataclasses
import json
import logging
import pathlib
import uuid
from collections.abc import Mapping

from agent_toolkit._agents_server.retained_results import read_retained_result
from agent_toolkit._agents_server.shared_layout import (
    aliases_directory,
    hosts_directory,
    list_root_session_ids,
    list_status_files,
    status_directory,
    valid_session_id,
)
from agent_toolkit._common import delegated_session as _delegated_session
from agent_toolkit._common.atomic_file import atomic_write
from agent_toolkit._common.next_action import ActionableError


@dataclasses.dataclass(frozen=True)
class StatusFileIdentity:
    """状態ファイルのルートと書込主体を表す。"""

    root_session_id: str
    file_name: str
    host_session_id: str | None


@dataclasses.dataclass(frozen=True)
class ConversationRootResolution:
    """現行会話から解決したルートと、その対応を確認できた根拠を表す。"""

    current_session_id: str
    root_session_id: str
    alias_present: bool
    alias_valid: bool
    mapping_confirmed: bool


def resolve_root_session_id(environment: Mapping[str, str]) -> str | None:
    """環境変数から読取対象のルートsessionを解決する。"""
    owner = _delegated_session.owner_session_id(environment) or environment.get("CLAUDE_CODE_SESSION_ID")
    return owner if owner is not None and valid_session_id(owner) else None


def resolve_status_file_identity(environment: Mapping[str, str]) -> StatusFileIdentity | None:
    """環境変数から状態ファイルの書込主体を解決し、識別できない場合は`None`を返す。"""
    owner = resolve_root_session_id(environment)
    if owner is None:
        return None

    host_session_id: str | None = None
    if environment.get(_delegated_session.DELEGATED_SESSION_ENV):
        host_session_id = environment.get("CLAUDE_CODE_SESSION_ID")
    elif environment.get("CODEX_THREAD_ID"):
        host_session_id = environment.get("CODEX_THREAD_ID")
    elif environment.get("AGENT_TOOLKIT_STATUS_HOST_SESSION"):
        host_session_id = environment.get("AGENT_TOOLKIT_STATUS_HOST_SESSION")
    if host_session_id is not None and not valid_session_id(host_session_id):
        return None
    if host_session_id is None and _delegated_session.owner_session_id(environment) is not None:
        return None

    file_name = "root.json" if host_session_id is None else f"{host_session_id}.json"
    return StatusFileIdentity(owner, file_name, host_session_id)


PROCESS_ROOT_PREFIX = "mcp-"
"""環境から所有会話を解決できないMCPプロセス専用のルート識別子の接頭辞。"""


def create_process_root_identity() -> StatusFileIdentity:
    """環境から所有会話を解決できないMCPプロセス専用のルート識別子を生成する。"""
    return StatusFileIdentity(f"{PROCESS_ROOT_PREFIX}{uuid.uuid4().hex}", "root.json", None)


def find_root_session_id_for_session(session_id: str, state_root: pathlib.Path | None = None) -> str | None:
    """共有状態から指定sessionを保持する一意なルートsession識別子を返す。"""
    if not valid_session_id(session_id):
        return None
    matches: list[str] = []
    for root_session_id in list_root_session_ids(state_root):
        if read_retained_result(root_session_id, session_id, state_root) is not None:
            matches.append(root_session_id)
            continue
        for path in list_status_files(root_session_id, state_root):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                continue
            sessions = payload.get("sessions") if isinstance(payload, dict) else None
            if isinstance(sessions, list) and any(
                isinstance(session, dict) and session.get("session_id") == session_id for session in sessions
            ):
                matches.append(root_session_id)
                break
    unique = set(matches)
    return matches[0] if len(unique) == 1 else None


def resolve_conversation_root(
    environment: Mapping[str, str],
    state_root: pathlib.Path | None = None,
) -> ConversationRootResolution | None:
    """現行会話のルートsession識別子と対応の確認状態を返す。"""
    current_session_id = resolve_root_session_id(environment)
    if current_session_id is None:
        return None
    alias_path = aliases_directory(state_root) / f"{current_session_id}.json"
    alias_present = alias_path.is_file()
    try:
        payload = json.loads(alias_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, UnicodeError, json.JSONDecodeError):
        direct = status_directory(current_session_id, state_root).is_dir()
        return ConversationRootResolution(
            current_session_id=current_session_id,
            root_session_id=current_session_id,
            alias_present=alias_present,
            alias_valid=False,
            mapping_confirmed=direct,
        )
    root_session_id = payload.get("root_session_id") if isinstance(payload, dict) else None
    if (
        isinstance(payload, dict)
        and payload.get("version") == 1
        and isinstance(root_session_id, str)
        and valid_session_id(root_session_id)
    ):
        alias_valid = True
        mapping_confirmed = status_directory(root_session_id, state_root).is_dir()
        if mapping_confirmed:
            resolved_root_session_id = root_session_id
        else:
            mapping_confirmed = status_directory(current_session_id, state_root).is_dir()
            resolved_root_session_id = current_session_id
    else:
        alias_valid = False
        mapping_confirmed = status_directory(current_session_id, state_root).is_dir()
        resolved_root_session_id = current_session_id
    return ConversationRootResolution(
        current_session_id=current_session_id,
        root_session_id=resolved_root_session_id,
        alias_present=True,
        alias_valid=alias_valid,
        mapping_confirmed=mapping_confirmed,
    )


def resolve_conversation_root_session_id(environment: Mapping[str, str], state_root: pathlib.Path | None = None) -> str | None:
    """現行会話のsession識別子を索引経由でルートsession識別子へ解決する。"""
    resolution = resolve_conversation_root(environment, state_root)
    return None if resolution is None else resolution.root_session_id


def unconfirmed_root_recovery(resolution: ConversationRootResolution, command: str) -> tuple[str, str]:
    """未確認のルートsessionを診断する理由と、MCPの一覧応答から復旧する次の操作を返す。"""
    if not resolution.alias_present:
        reason = "aliasが存在しません"
    elif not resolution.alias_valid:
        reason = "aliasの書式が不正です"
    else:
        reason = "aliasの参照先を確認できません"
    return (
        f"agents_serverのルートsession対応を確認できません。CLIが解決したroot={resolution.root_session_id}、{reason}。",
        f"MCPの`list`を1回呼び出してから`{command}`を再実行する",
    )


def write_root_alias(
    current_session_id: str,
    root_session_id: str,
    state_root: pathlib.Path | None = None,
) -> None:
    """現行session識別子のルート索引を書き、参照先を失った索引を回収する。"""
    if not valid_session_id(current_session_id) or not valid_session_id(root_session_id):
        raise ValueError("invalid session_id")
    directory = aliases_directory(state_root)
    if directory.exists():
        for path in directory.glob("*.json"):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                continue
            target = payload.get("root_session_id") if isinstance(payload, dict) else None
            if isinstance(target, str) and valid_session_id(target) and not status_directory(target, state_root).is_dir():
                path.unlink(missing_ok=True)
    payload = {"version": 1, "root_session_id": root_session_id}
    atomic_write(directory / f"{current_session_id}.json", json.dumps(payload, ensure_ascii=False) + "\n")


def resolve_status_owner_identity(
    environment: Mapping[str, str],
    state_root: pathlib.Path | None = None,
) -> StatusFileIdentity | None:
    """呼出主体を共有状態の書込主体へ解決し、対応が曖昧な場合は失敗する。"""
    identity = resolve_status_file_identity(environment)
    if identity is None or identity.host_session_id is None:
        return identity
    try:
        paths = tuple(hosts_directory(identity.root_session_id, state_root).iterdir())
    except FileNotFoundError:
        paths = ()
    except OSError as error:
        raise ActionableError(
            f"書込主体索引を読めません: {error}",
            next_action="索引ディレクトリの権限を確かめて再実行し、解消しない場合はagents_serverの不具合としてユーザーへ報告する",
        ) from error

    writers: list[str] = []
    for path in paths:
        if path.suffix != ".json" or not valid_session_id(path.stem):
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        if (
            isinstance(payload, dict)
            and payload.get("version") == 1
            and payload.get("host_session_id") == identity.host_session_id
        ):
            writers.append(path.stem)
    if not writers:
        for path in list_status_files(identity.root_session_id, state_root):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                continue
            if isinstance(payload, dict) and payload.get("host_session_id") == identity.host_session_id:
                writers.append(path.stem)
    if not writers:
        return identity
    if len(writers) != 1:
        raise ActionableError(
            "委譲元sessionに対応する書込主体を一意に解決できません: "
            f"host_session_id={identity.host_session_id}, writers={','.join(sorted(writers))}",
            next_action=(
                "同じsessionで`atk agents list`を実行して書込主体を確かめ、"
                "解消しない場合はagents_serverの不具合としてユーザーへ報告する"
            ),
        )
    writer_session_id = writers[0]
    return StatusFileIdentity(identity.root_session_id, f"{writer_session_id}.json", writer_session_id)


_EXPLICIT_ROOT_NEXT_ACTION = (
    "MCPの`list`が返す`root_session_id`を`--root-session-id`へ渡すか、`--root-session-id`を外して再実行する"
)


def resolve_wait_identity(
    environment: Mapping[str, str],
    explicit_root_session_id: str | None,
    state_root: pathlib.Path | None = None,
) -> StatusFileIdentity | None:
    """待機に使うルートと書込主体を、明示入力を含む単一の契約で解決する。"""
    inferred = resolve_conversation_root(environment, state_root)
    identity = resolve_status_owner_identity(environment, state_root)
    if explicit_root_session_id is None:
        if identity is None or inferred is None or identity.root_session_id == inferred.root_session_id:
            return identity
        return StatusFileIdentity(inferred.root_session_id, identity.file_name, identity.host_session_id)
    if not valid_session_id(explicit_root_session_id):
        raise ActionableError(
            f"root_session_idの形式が不正です: {explicit_root_session_id}", next_action=_EXPLICIT_ROOT_NEXT_ACTION
        )
    if not status_directory(explicit_root_session_id, state_root).is_dir():
        raise ActionableError(
            f"指定したroot_session_idの状態ディレクトリが存在しません: {explicit_root_session_id}",
            next_action=_EXPLICIT_ROOT_NEXT_ACTION,
        )

    if inferred is not None and inferred.mapping_confirmed and inferred.root_session_id != explicit_root_session_id:
        raise ActionableError(
            "確認済みのルートsessionと指定したroot_session_idが一致しません: "
            f"conversation={inferred.root_session_id}, explicit={explicit_root_session_id}",
            next_action="`--root-session-id`を外して再実行する",
        )
    if identity is not None:
        return StatusFileIdentity(explicit_root_session_id, identity.file_name, identity.host_session_id)
    return StatusFileIdentity(explicit_root_session_id, "root.json", None)


def write_host_alias(
    root_session_id: str,
    writer_session_id: str,
    host_session_id: str,
    state_root: pathlib.Path | None = None,
) -> None:
    """書込主体を委譲元threadへ対応付ける索引を書く。"""
    if not all(valid_session_id(value) for value in (root_session_id, writer_session_id, host_session_id)):
        raise ValueError("invalid session_id")
    payload = {"version": 1, "host_session_id": host_session_id}
    atomic_write(
        hosts_directory(root_session_id, state_root) / f"{writer_session_id}.json",
        json.dumps(payload, ensure_ascii=False) + "\n",
    )


_LOG = logging.getLogger("agent-toolkit.agents-server.status-file")

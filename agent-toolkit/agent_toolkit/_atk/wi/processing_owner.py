from __future__ import annotations

import dataclasses
import json
import pathlib
import socket
from collections.abc import Iterable

from agent_toolkit._atk.wi import frontmatter
from agent_toolkit._atk.wi.constants import WI_STATE_PROCESSING
from agent_toolkit._atk.wi.web_input import WebInputError
from agent_toolkit._common import process_identity, session_activity, session_state
from agent_toolkit._git import command as git
from agent_toolkit._plan.owner_records import resolve_conversation_session_id

KEY = "processing_owner"
_PROCESS_EVIDENCE = "wi_processing_owner_process"
EXIT_ACTIONS = frozenset({"adopt", "reject", "return-to-inbox", "hold"})
_NEXT = "担当の会話から再実行するか、前の会話の終了を確認してから `atk wi take-over <ファイル名>` で引き継ぐ"


@dataclasses.dataclass(frozen=True)
class Owner:
    host: str
    session: str

    def __str__(self) -> str:
        return f"{self.host}/{self.session}"


def current() -> Owner | None:
    session = resolve_conversation_session_id()
    return Owner(socket.gethostname(), session) if session else None


def remember_process(owner: Owner) -> None:
    identity = session_activity.recorded_root_process(owner.session)
    if identity is not None:
        payload = {"session": owner.session, "process": identity.payload()}
        session_state.update_state(
            owner.session,
            lambda state: None if state.get(_PROCESS_EVIDENCE) == payload else {**state, _PROCESS_EVIDENCE: payload},
        )


def _stop_status(session: str) -> str:
    status = session_activity.root_activity(session)["status"]
    if isinstance(status, str) and status != "不明":
        return status
    if (identity := session_activity.recorded_root_process(session)) is not None and identity.exists():
        return "稼働中か不明"
    try:
        evidence = session_state.read_state(session).get(_PROCESS_EVIDENCE)
        if not isinstance(evidence, dict) or evidence.get("session") != session:
            return "不明"
        identity = process_identity.ProcessIdentity.parse(evidence.get("process"))
    except ValueError, OSError:
        return "不明"
    return "稼働中か不明" if identity.exists() else "終了"


def from_text(text: str) -> Owner | None:
    parsed = frontmatter.parse_frontmatter(text)
    if parsed is None or parsed[0].get("state") != WI_STATE_PROCESSING or KEY not in parsed[0]:
        return None
    raw = parsed[0][KEY]
    if (
        isinstance(raw, dict)
        and isinstance(host := raw.get("host"), str)
        and host
        and isinstance(session := raw.get("session"), str)
        and session
    ):
        return Owner(host, session)
    raise WebInputError("処理中の担当の記録が不正です", next_action="保存済みWIのprocessing_ownerを確認する")


def snapshot(paths: Iterable[pathlib.Path]) -> dict[pathlib.Path, Owner]:
    result: dict[pathlib.Path, Owner] = {}
    for path in paths:
        if (owner := from_text(path.read_text(encoding="utf-8"))) is not None:
            result[path] = owner
    return result


def verify_actor(owners: dict[pathlib.Path, Owner], actor: Owner | None) -> None:
    for path, owner in owners.items():
        if owner != actor:
            raise WebInputError(f"{path.name}の担当は{owner}です。現在の会話は担当ではありません", next_action=_NEXT)


def verify_upstream(private_notes: pathlib.Path, owners: dict[pathlib.Path, Owner]) -> None:
    for path, previous in owners.items():
        result = git.run(
            ["show", f"@{{u}}:{path.relative_to(private_notes).as_posix()}"],
            private_notes,
            check=False,
            capture_output=True,
            text=True,
        )
        actual = from_text(result.stdout) if result.returncode == 0 else None
        if actual != previous:
            raise WebInputError(
                f"{path.name}のremote上の状態または担当が変わりました（照合時: {previous}、現在: {actual or '担当なし'}）",
                next_action="`atk wi show --pull`で現在の状態と担当を確認する。処理中なら " + _NEXT,
            )


def takeover_note(path: pathlib.Path, previous: Owner, actor: Owner, reason: str | None) -> str:
    if previous == actor:
        raise WebInputError(f"{path.name}は既に現在の会話が担当しています", next_action="引き継がずに作業を続ける")
    if previous.host == actor.host:
        status = _stop_status(previous.session)
        if status not in {"終了", "不明"}:
            raise WebInputError(
                f"{path.name}の担当 {previous} の終了を確認できません（{status}）",
                next_action="前の担当の終了を待つか、前の担当を止めてから同じ引き継ぎ操作を再実行する",
            )
        evidence = "記録されたホストプロセスの終了を確認" if status == "終了" else "稼働中のホストプロセスを確認できなかった"
        if reason and reason.strip():
            evidence += "。指定された理由: " + json.dumps(reason.strip(), ensure_ascii=False)
    elif reason and reason.strip():
        evidence = json.dumps(reason.strip(), ensure_ascii=False)
    else:
        raise WebInputError(
            f"{path.name}の担当 {previous} は別のホストです",
            next_action="前の会話が終了したことを確認し、確認した方法を --reason に指定して引き継ぐ",
        )
    return f"- 担当の引き継ぎ: {previous} → {actor}（{evidence}）"

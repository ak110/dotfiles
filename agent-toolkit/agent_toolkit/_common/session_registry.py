from __future__ import annotations

import contextlib
import dataclasses
import datetime
import json
import os
import pathlib
import re
from collections.abc import Generator, Sequence
from typing import Any, Literal

from agent_toolkit._common import file_lock, private_directory, reclaim_records, registry_forwarding
from agent_toolkit._common import session_launchers as _session_launchers
from agent_toolkit._common import state_paths as _state_paths
from agent_toolkit._common.atomic_file import atomic_write
from agent_toolkit._common.process_identity import ProcessIdentity
from agent_toolkit._common.session_registry_state import PublicationState, Resolution, RunningOwnership
from agent_toolkit._common.startup_features import StartupProfile

_SESSION_ID_PATTERN = re.compile(r"^[0-9A-Za-z_-]+$")
_STATUSES = frozenset({"starting", "running", "completed", "failed", "interrupted"})
ReleaseReason = Literal["retention_expired", "stopped"]
_RELEASE_REASONS: frozenset[str] = frozenset({"retention_expired", "stopped"})
LAUNCHER_KEY = _session_launchers.LAUNCHER_KEY
InterruptionReason = Literal["owner_processes_exited", "owner_server_stopped"]
OWNER_PROCESSES_EXITED: InterruptionReason = "owner_processes_exited"
OWNER_SERVER_STOPPED: InterruptionReason = "owner_server_stopped"
_INTERRUPTION_MESSAGES: dict[str, str] = {
    OWNER_PROCESSES_EXITED: (
        "the owning agents_server exited without publishing the terminal state, and no process running the turn remains"
    ),
    OWNER_SERVER_STOPPED: "the owning agents_server stopped while the turn was running and published it as interrupted",
}
INTERRUPTED_TURNS_LIMIT = 16
"""登録簿が保持する中断turnの履歴の件数。新しい順に保ち、超えた古い要素は参照ごと失う。"""


_OWNER_PROCESS: ProcessIdentity | None = None
_EFFORT_RECEIVER: ProcessIdentity | None = None


def declare_effort_receiver() -> None:
    global _EFFORT_RECEIVER  # pylint: disable=global-statement
    _EFFORT_RECEIVER = owner_process()


def owner_process() -> ProcessIdentity | None:
    global _OWNER_PROCESS  # pylint: disable=global-statement
    if selected := registry_forwarding.identity():
        return selected.owner
    if _OWNER_PROCESS is None or _OWNER_PROCESS.pid != os.getpid():
        _OWNER_PROCESS = ProcessIdentity.of(os.getpid())
    return _OWNER_PROCESS


_OWNER_SERVER_INSTANCE: str | None = None


def set_owner_server_instance(instance_id: str | None) -> None:
    global _OWNER_SERVER_INSTANCE  # pylint: disable=global-statement
    _OWNER_SERVER_INSTANCE = instance_id


def owner_server_instance() -> str | None:
    selected = registry_forwarding.identity()
    return _OWNER_SERVER_INSTANCE if selected is None else selected.server_instance


def effort_receiver() -> ProcessIdentity | None:
    selected = registry_forwarding.identity()
    return _EFFORT_RECEIVER if selected is None else selected.effort_receiver


def owner_process_is_cached() -> bool:
    return registry_forwarding.identity() is not None or _OWNER_PROCESS is not None and _OWNER_PROCESS.pid == os.getpid()


def interruption_error(reason: InterruptionReason) -> dict[str, str]:
    return {"message": _INTERRUPTION_MESSAGES[reason], "interruptionReason": reason}


@dataclasses.dataclass(frozen=True)
class LaunchInfo:
    label: str = ""
    prompt: str = ""

    created_at: str | None = None
    followup_prompts: tuple[str, ...] = ()
    final_responses: tuple[tuple[int, str], ...] = ()
    terminal_error: str | None = None
    effort_history: tuple[tuple[str, str, str, str], ...] = ()
    effort_levels: tuple[str, ...] = ()

    @classmethod
    def of(cls, source: object) -> LaunchInfo:
        return cls(**{field.name: getattr(source, field.name) for field in dataclasses.fields(cls)})

    @classmethod
    def from_record(cls, payload: dict[str, Any]) -> LaunchInfo:
        values = {
            field.name: payload[field.name] for field in dataclasses.fields(cls) if isinstance(payload.get(field.name), str)
        }
        for name in ("followup_prompts", "final_responses", "effort_history", "effort_levels"):
            values.pop(name, None)
        followups = payload.get("followup_prompts", [])
        responses = payload.get("final_responses", [])
        history = payload.get("effort_history", [])
        levels = payload.get("effort_levels", [])
        return cls(
            **values,
            effort_history=tuple(
                (item[0], item[1], item[2], item[3])
                for item in history
                if isinstance(item, list) and len(item) == 4 and all(isinstance(value, str) for value in item)
            )
            if isinstance(history, list)
            else (),
            effort_levels=tuple(item for item in levels if isinstance(item, str)) if isinstance(levels, list) else (),
            followup_prompts=tuple(item for item in followups if isinstance(item, str)) if isinstance(followups, list) else (),
            final_responses=tuple(
                (item[0], item[1])
                for item in responses
                if isinstance(item, list)
                and len(item) == 2
                and isinstance(item[0], int)
                and not isinstance(item[0], bool)
                and isinstance(item[1], str)
            )
            if isinstance(responses, list)
            else (),
        )

    def as_kwargs(self) -> dict[str, Any]:
        return {field.name: getattr(self, field.name) for field in dataclasses.fields(self)}

    def apply_to(self, target: object) -> None:
        for name, value in self.as_kwargs().items():
            if value is not None:
                setattr(target, name, value)


@dataclasses.dataclass(frozen=True)
class ResumeInfo:
    engine: str
    cwd: str
    model: str | None
    effort: str | None
    model_type: str | None
    launch_kind: Literal["delegate", "explore", "shell", "write"]
    turn_seq: int
    status: Literal["starting", "running", "completed", "failed", "interrupted"]
    launch_info: LaunchInfo = LaunchInfo()

    started_at: str | None = None
    session_updated_at: str | None = None
    # 最後に公開した時点のturnを表すengine固有の識別子（Codexのturn id）。
    # 再起動後に残存記録の終了を委譲先CLIの記録と照らすために使う。項目を持たない記録とCodex以外では`None`とする。
    turn_id: str | None = None
    fast_mode: bool | None = None
    codex_model_provider: str | None = None
    codex_subscription_provider: str | None = None
    codex_state_provider: str | None = None
    claude_api_env_file: str | None = None
    claude_connection: str | None = None
    claude_role: str | None = None
    result_contract_recovery_consumed: bool = False
    startup_profile: StartupProfile | None = None


@dataclasses.dataclass(frozen=True)
class InterruptedTurn:
    turn_seq: int
    reason: InterruptionReason
    server_instance: str | None
    at: str


@dataclasses.dataclass(frozen=True)
class SessionResolution:
    state: Resolution
    resume_info: ResumeInfo | None = None

    released_reason: ReleaseReason | None = None
    released_at: str | None = None

    interruption: InterruptionReason | None = None

    inferred_terminal: bool = False
    handoff_info: ResumeInfo | None = None

    server_instance: str | None = None

    recorded_turn_seq: int | None = None

    interrupted_turns: tuple[InterruptedTurn, ...] = ()
    # 記録プロセス。`processes`の先頭が所有サーバー、以降が委譲先CLIである。項目を持たない記録では空とする。
    owner_process: ProcessIdentity | None = None
    executor_processes: tuple[ProcessIdentity, ...] = ()
    # `running`の記録で、解決の時点で実在した委譲先CLI。終了の記録では照会しないため空とする。
    live_executors: tuple[ProcessIdentity, ...] = ()

    owner_root_session_id: str | None = None
    resume_pending: bool = False


def running_ownership(resolution: SessionResolution, *, writer_alive: bool) -> RunningOwnership | None:
    if resolution.state is not Resolution.RUNNING:
        return None
    if writer_alive:
        return RunningOwnership.OWNED
    return RunningOwnership.OWNER_LOST if resolution.live_executors else RunningOwnership.ORPHANED


def process_payload(process: ProcessIdentity) -> dict[str, Any]:
    return process.payload()


def registry_directory(state_root: pathlib.Path | None = None) -> pathlib.Path:
    return _session_launchers.registry_directory(_state_paths.state_dir() if state_root is None else state_root)


@contextlib.contextmanager
def registry_lock(session_id: str, *, state_root: pathlib.Path | None = None) -> Generator[None]:
    """公開・比較更新・掃引をsession単位で直列化する。ロック実体は削除せず同じinodeを共有する。"""
    _validate_session_id(session_id)
    directory = registry_directory(state_root) / ".locks"
    directory.mkdir(mode=private_directory.MODE, parents=True, exist_ok=True)
    with (directory / f"{session_id}.lock").open("a+", encoding="utf-8") as handle:
        file_lock.acquire_lock(handle)
        try:
            yield
        finally:
            file_lock.release_lock(handle)


def _recorded_turn(payload: dict[str, Any]) -> int | None:
    value = payload.get("turn_seq")
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    for key in ("handoff_info", "codex_resume_info", "claude_resume_info"):
        nested = payload.get(key)
        if isinstance(nested, dict):
            value = nested.get("turn_seq")
            if isinstance(value, int) and not isinstance(value, bool):
                return value
    return None


def publish(
    session_id: str,
    *,
    state: PublicationState,
    engine: str = "codex",
    cwd: str = "",
    model: str | None = None,
    effort: str | None = None,
    fast_mode: bool | None = None,
    codex_model_provider: str | None = None,
    codex_subscription_provider: str | None = None,
    codex_state_provider: str | None = None,
    claude_api_env_file: str | None = None,
    claude_connection: str | None = None,
    claude_role: str | None = None,
    model_type: str | None = None,
    launch_kind: Literal["delegate", "explore", "shell", "write"] = "delegate",
    startup_profile: StartupProfile | None = None,
    turn_seq: int = 0,
    launch_info: LaunchInfo | None = None,
    started_at: str | None = None,
    session_updated_at: str | None = None,
    turn_id: str | None = None,
    processes: Sequence[ProcessIdentity] = (),
    interruption: InterruptionReason | None = None,
    launcher_session_id: str | None = None,
    preserve_server_instance: bool = False,
    owner_root_session_id: str | None = None,
    result_contract_recovery_consumed: bool = False,
    state_root: pathlib.Path | None = None,
) -> None:
    forwarded, _result = registry_forwarding.forward("publish", locals())
    if forwarded:
        return
    _validate_session_id(session_id)
    if not isinstance(state, PublicationState):
        raise TypeError("state must be a PublicationState")
    updated_at = datetime.datetime.now(datetime.UTC).isoformat()
    payload = {
        "version": 2,
        "session_id": session_id,
        "terminal": state.terminal,
        "engine": engine,
        "cwd": cwd,
        "model": model,
        "effort": effort,
        "model_type": model_type,
        "launch_kind": launch_kind,
        "startup_profile": startup_profile,
        "turn_seq": turn_seq,
        "status": state.status,
        "updated_at": updated_at,
    }
    if launch_info is not None:
        payload.update((name, value) for name, value in launch_info.as_kwargs().items() if value is not None)
    if started_at is not None:
        payload["started_at"] = started_at
    if session_updated_at is not None:
        payload["session_updated_at"] = session_updated_at
    if turn_id:
        payload["turn_id"] = turn_id
    if processes:
        payload["processes"] = [process_payload(process) for process in processes]

        receiver = effort_receiver()
        if (
            receiver is not None
            and processes[0] == receiver
            and (receiver.pid == os.getpid() or registry_forwarding.identity() is not None)
        ):
            payload["effort_receiver"] = {"version": 1, "process": process_payload(receiver)}
    if interruption is not None:
        payload["interruption"] = interruption
    if engine == "codex" and fast_mode is not None:
        payload["fast_mode"] = fast_mode
    if engine == "codex" and codex_model_provider:
        payload["codex_model_provider"] = codex_model_provider
        payload["codex_subscription_provider"] = codex_subscription_provider
    if engine == "codex" and codex_state_provider:
        payload["codex_state_provider"] = codex_state_provider
    if engine == "claude" and claude_api_env_file:
        payload["claude_api_env_file"] = claude_api_env_file
    if engine == "claude" and claude_connection:
        payload["claude_connection"] = claude_connection
        payload["claude_role"] = claude_role
    path = registry_directory(state_root) / f"{session_id}.json"
    with registry_lock(session_id, state_root=state_root):
        prior = _read_payload(path)
        if not _proxy_owner_matches(prior, allow_claim=True):
            return
        claim = prior.get("resume_claim")
        if (
            isinstance(claim, dict)
            and (claimed_turn := _recorded_turn(claim)) is not None
            and turn_seq <= claimed_turn
            and any(process.exists() for process in _recorded_processes(claim))
        ):
            return
        previous_turn = _recorded_turn(prior)
        if previous_turn is not None and (previous_turn > turn_seq or prior.get("version") == 3 and previous_turn == turn_seq):
            return
        if launch_info is not None:
            source = prior.get("handoff_info", prior.get("codex_resume_info", prior.get("claude_resume_info", prior)))
            if isinstance(source, dict):
                merged = _preserve_launch_info(launch_info, LaunchInfo.from_record(source))
                payload.update((name, value) for name, value in merged.as_kwargs().items() if value is not None)

        if result_contract_recovery_consumed or prior.get("result_contract_recovery_consumed") is True:
            payload["result_contract_recovery_consumed"] = True
        owner_root = owner_root_session_id if owner_root_session_id is not None else _recorded_owner_root(prior)
        if owner_root is not None:
            payload["owner_root_session_id"] = owner_root
        instance = prior.get("server_instance") if preserve_server_instance else owner_server_instance()
        if isinstance(instance, str) and instance:
            payload["server_instance"] = instance
        turns = _recorded_interrupted_turns(prior)
        if interruption is not None:
            recorded_instance = instance if isinstance(instance, str) and instance else None
            turns = _with_interrupted_turn(turns, InterruptedTurn(turn_seq, interruption, recorded_instance, updated_at))
        if turns:
            payload["interrupted_turns"] = _interrupted_turns_payload(turns)
        launcher = launcher_session_id if launcher_session_id is not None else _recorded_launcher(path)
        if launcher is not None:
            payload[LAUNCHER_KEY] = launcher
        atomic_write(path, json.dumps(payload, ensure_ascii=False) + "\n")


def _preserve_launch_info(current: LaunchInfo, previous: LaunchInfo) -> LaunchInfo:
    followups = current.followup_prompts
    saved = previous.followup_prompts
    if saved[: len(followups)] == followups:
        followups = saved
    elif followups[: len(saved)] != saved:
        followups = saved + followups
    responses = dict(previous.final_responses)
    responses.update(current.final_responses)
    return dataclasses.replace(
        current,
        label=previous.label or current.label,
        prompt=previous.prompt or current.prompt,
        created_at=previous.created_at or current.created_at,
        followup_prompts=followups,
        final_responses=tuple(responses.items()),
        effort_history=tuple(dict.fromkeys((*previous.effort_history, *current.effort_history))),
        effort_levels=current.effort_levels or previous.effort_levels,
    )


_recorded_launcher = _session_launchers.read_launcher


def claim_resume(session_id: str, turn_seq: int, *, state_root: pathlib.Path | None = None) -> bool:
    """同じ終了turnの再開を1プロセスだけに予約し、ロックは起動前に解放する。

    元の終了状態は予約中も保持する。予約したプロセスが消失した場合は次の再開が予約を取得できる。
    登録を持たないローカル保持状態は、既存のManager内の再開排他を使う。
    """
    forwarded, result = registry_forwarding.forward("claim_resume", locals())
    if forwarded:
        return bool(result)
    _validate_session_id(session_id)
    with registry_lock(session_id, state_root=state_root):
        resolution = resolve(session_id, state_root=state_root)
        if resolution.state in {Resolution.RUNNING, Resolution.UNREADABLE}:
            return False
        if resolution.recorded_turn_seq is not None and resolution.recorded_turn_seq != turn_seq:
            return False
        path = registry_directory(state_root) / f"{session_id}.json"
        payload = _read_payload(path)
        if not payload:
            return True
        claim = payload.get("resume_claim")
        if isinstance(claim, dict) and any(process.exists() for process in _recorded_processes(claim)):
            return False
        process = owner_process()
        if process is None:
            raise RuntimeError("再開を所有するプロセスの識別を取得できない")
        payload["resume_claim"] = {"turn_seq": turn_seq, "processes": [process_payload(process)]}
        atomic_write(path, json.dumps(payload, ensure_ascii=False) + "\n", fsync=True)
        return True


def transfer_owner(
    session_id: str,
    expected_owner: tuple[str | None, int | None],
    new_owner: ProcessIdentity,
    new_server_instance: str,
    *,
    state_root: pathlib.Path | None = None,
) -> bool:
    forwarded, result = registry_forwarding.forward("transfer_owner", locals())
    if forwarded:
        return bool(result)
    _validate_session_id(session_id)
    _validate_session_id(new_server_instance)
    if not isinstance(new_owner, ProcessIdentity):
        raise TypeError("新しい所有者のプロセス識別が不正です")
    with registry_lock(session_id, state_root=state_root):
        path = registry_directory(state_root) / f"{session_id}.json"
        payload = _read_payload(path)
        if payload.get("version") != 2 or expected_owner != (
            _recorded_server_instance(payload),
            _recorded_turn(payload),
        ):
            return False
        processes = _recorded_processes(payload)
        payload["processes"] = [process_payload(new_owner), *(process_payload(item) for item in processes[1:])]
        payload["server_instance"] = new_server_instance
        payload["effort_receiver"] = {"version": 1, "process": process_payload(new_owner)}
        payload["updated_at"] = datetime.datetime.now(datetime.UTC).isoformat()
        atomic_write(path, json.dumps(payload, ensure_ascii=False) + "\n", fsync=True)
        return True


def release_resume_claim(session_id: str, turn_seq: int, *, state_root: pathlib.Path | None = None) -> None:
    forwarded, _result = registry_forwarding.forward("release_resume_claim", locals())
    if forwarded:
        return
    _validate_session_id(session_id)
    with registry_lock(session_id, state_root=state_root):
        path = registry_directory(state_root) / f"{session_id}.json"
        payload = _read_payload(path)
        claim = payload.get("resume_claim")
        if isinstance(claim, dict) and claim.get("turn_seq") == turn_seq and _recorded_processes(claim) == (owner_process(),):
            payload.pop("resume_claim")
            atomic_write(path, json.dumps(payload, ensure_ascii=False) + "\n", fsync=True)


def resolve(session_id: str, *, state_root: pathlib.Path | None = None) -> SessionResolution:
    _validate_session_id(session_id)
    path = registry_directory(state_root) / f"{session_id}.json"
    try:
        payload: Any = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return SessionResolution(Resolution.MISSING)
    except OSError, UnicodeError, json.JSONDecodeError:
        return SessionResolution(Resolution.UNREADABLE)
    return resolve_payload(session_id, payload)


def resolve_payload(session_id: str, payload: Any) -> SessionResolution:
    if (
        not isinstance(payload, dict)
        or payload.get("session_id") != session_id
        or not isinstance(payload.get("updated_at"), str)
    ):
        return SessionResolution(Resolution.UNREADABLE)
    if payload.get("version") == 3:
        reason = payload.get("released_reason")
        if reason not in _RELEASE_REASONS:
            return SessionResolution(Resolution.UNREADABLE)
        resume = payload.get("codex_resume_info")
        info = _resume_info(resume) if isinstance(resume, dict) and resume.get("engine") == "codex" else None
        if info is None:
            resume = payload.get("claude_resume_info")
            info = _resume_info(resume) if isinstance(resume, dict) and resume.get("engine") == "claude" else None
        handoff = payload.get("handoff_info")
        nested = handoff if isinstance(handoff, dict) else resume if isinstance(resume, dict) else {}
        return _with_resume_claim(
            payload,
            SessionResolution(
                Resolution.RELEASED,
                info,
                released_reason=reason,
                released_at=payload["updated_at"],
                handoff_info=_resume_info(handoff) if isinstance(handoff, dict) else None,
                server_instance=_recorded_server_instance(payload),
                recorded_turn_seq=_recorded_turn(payload),
                interrupted_turns=_recorded_interrupted_turns(payload),
                owner_root_session_id=_recorded_owner_root(payload) or _recorded_owner_root(nested),
            ),
        )
    if payload.get("version") != 2 or not isinstance(payload.get("terminal"), bool):
        return SessionResolution(Resolution.UNREADABLE)
    info = _resume_info(payload)
    if info is None:
        return SessionResolution(Resolution.UNREADABLE)
    instance = _recorded_server_instance(payload)
    turns = _recorded_interrupted_turns(payload)
    processes = _recorded_processes(payload)
    owner = processes[0] if processes else None
    executors = processes[1:]
    owner_root = _recorded_owner_root(payload)
    if isinstance(payload.get("resume_claim"), dict):
        pending = _with_resume_claim(
            payload,
            SessionResolution(
                Resolution.TERMINAL if payload["terminal"] else Resolution.RUNNING,
                info,
                server_instance=instance,
                recorded_turn_seq=_recorded_turn(payload),
                interrupted_turns=turns,
                owner_root_session_id=owner_root,
            ),
        )
        if pending.resume_pending:
            return pending
    if payload["terminal"]:
        interruption = payload.get("interruption")
        return _with_resume_claim(
            payload,
            SessionResolution(
                Resolution.TERMINAL,
                info,
                interruption=interruption if interruption in _INTERRUPTION_MESSAGES else None,
                server_instance=instance,
                recorded_turn_seq=_recorded_turn(payload),
                interrupted_turns=turns,
                owner_process=owner,
                executor_processes=executors,
                owner_root_session_id=owner_root,
            ),
        )
    # 起点が終了しても回収待ちの子孫が残る委譲先CLIは、停止の再試行で回収するまで生存中として扱い、
    # 終了の推定と公開を止める（停止側は`recorded_process.stop_recorded_executor`）。
    alive = [process for process in processes if process.exists() or reclaim_records.pending(process.pid, process.create_time)]
    if processes and not alive:
        return SessionResolution(
            Resolution.TERMINAL,
            dataclasses.replace(info, status="interrupted"),
            interruption=OWNER_PROCESSES_EXITED,
            inferred_terminal=True,
            server_instance=instance,
            recorded_turn_seq=_recorded_turn(payload),
            interrupted_turns=turns,
            owner_process=owner,
            executor_processes=executors,
            owner_root_session_id=owner_root,
        )
    return SessionResolution(
        Resolution.RUNNING,
        info,
        server_instance=instance,
        recorded_turn_seq=_recorded_turn(payload),
        interrupted_turns=turns,
        owner_process=owner,
        executor_processes=executors,
        live_executors=tuple(process for process in executors if process in alive),
        owner_root_session_id=owner_root,
    )


def _with_resume_claim(payload: dict[str, Any], resolution: SessionResolution) -> SessionResolution:
    claim = payload.get("resume_claim")
    info = resolution.resume_info or resolution.handoff_info
    if not isinstance(claim, dict) or info is None or _recorded_turn(claim) != info.turn_seq:
        return resolution
    claimed = _recorded_processes(claim)
    if not claimed or not any(process.exists() for process in claimed):
        return resolution
    return dataclasses.replace(
        resolution,
        state=Resolution.RUNNING,
        resume_info=dataclasses.replace(info, status="starting", turn_seq=info.turn_seq + 1),
        owner_process=claimed[0],
        inferred_terminal=False,
        interruption=None,
        resume_pending=True,
    )


def _recorded_processes(payload: dict[str, Any]) -> tuple[ProcessIdentity, ...]:
    entries = payload.get("processes")
    if not isinstance(entries, list) or not entries:
        return ()
    processes: list[ProcessIdentity] = []
    for entry in entries:
        pid = entry.get("pid") if isinstance(entry, dict) else None
        create_time = entry.get("create_time") if isinstance(entry, dict) else None
        if (
            not isinstance(pid, int)
            or isinstance(pid, bool)
            or not isinstance(create_time, (int, float))
            or isinstance(create_time, bool)
        ):
            return ()
        processes.append(ProcessIdentity(pid, float(create_time), entry.get("start_id")))
    return tuple(processes)


def release(
    session_id: str,
    *,
    reason: ReleaseReason,
    state_root: pathlib.Path | None = None,
    expected_owner: tuple[str | None, int | None] | None = None,
) -> bool:
    forwarded, result = registry_forwarding.forward("release", locals())
    if forwarded:
        return bool(result)
    _validate_session_id(session_id)
    if reason not in _RELEASE_REASONS:
        raise ValueError(f"invalid release reason: {reason}")
    with registry_lock(session_id, state_root=state_root):
        path = registry_directory(state_root) / f"{session_id}.json"
        prior_payload = _read_payload(path)
        if not _proxy_owner_matches(prior_payload):
            return False
        if resolve(session_id, state_root=state_root).resume_pending:
            return False
        if expected_owner is not None and expected_owner != (
            _recorded_server_instance(prior_payload),
            _recorded_turn(prior_payload),
        ):
            return False
        payload: dict[str, Any] = {
            "version": 3,
            "session_id": session_id,
            "released_reason": reason,
            "turn_seq": _recorded_turn(prior_payload),
            "updated_at": datetime.datetime.now(datetime.UTC).isoformat(),
        }
        launcher = _recorded_launcher(path)
        resolution = resolve(session_id, state_root=state_root)
        info = resolution.resume_info or resolution.handoff_info
        if info is not None and info.launch_info.prompt:
            payload["handoff_info"] = prior_payload.get(
                "handoff_info", prior_payload.get("codex_resume_info", prior_payload.get("claude_resume_info", prior_payload))
            )
        if (
            info is not None
            and info.engine == "codex"
            and (
                info.codex_state_provider
                or info.codex_model_provider
                and info.codex_model_provider != info.codex_subscription_provider
            )
        ):
            payload["codex_resume_info"] = prior_payload.get("codex_resume_info", prior_payload)
        if info is not None and info.engine == "claude" and (info.claude_api_env_file or info.claude_connection):
            payload["claude_resume_info"] = prior_payload.get("claude_resume_info", prior_payload)
        instance = _recorded_server_instance(prior_payload)
        if instance is not None:
            payload["server_instance"] = instance
        turns = _recorded_interrupted_turns(prior_payload)
        if turns:
            payload["interrupted_turns"] = _interrupted_turns_payload(turns)
        if launcher is not None:
            payload[LAUNCHER_KEY] = launcher
        atomic_write(path, json.dumps(payload, ensure_ascii=False) + "\n")
        return True


def record_interrupted_turn(
    session_id: str, *, turn_seq: int, reason: InterruptionReason, state_root: pathlib.Path | None = None
) -> None:
    forwarded, _result = registry_forwarding.forward("record_interrupted_turn", locals())
    if forwarded:
        return
    _validate_session_id(session_id)
    with registry_lock(session_id, state_root=state_root):
        path = registry_directory(state_root) / f"{session_id}.json"
        payload = _read_payload(path)
        if not _proxy_owner_matches(payload):
            return
        if payload.get("version") != 2 or payload.get("terminal") is not True or payload.get("turn_seq") != turn_seq:
            return
        payload["interruption"] = reason
        payload["interrupted_turns"] = _interrupted_turns_payload(
            _with_interrupted_turn(
                _recorded_interrupted_turns(payload),
                InterruptedTurn(
                    turn_seq, reason, _recorded_server_instance(payload), datetime.datetime.now(datetime.UTC).isoformat()
                ),
            )
        )
        atomic_write(path, json.dumps(payload, ensure_ascii=False) + "\n")


def _proxy_owner_matches(payload: dict[str, Any], *, allow_claim: bool = False) -> bool:
    """移譲後の旧writerを、保存と同じ登録ロックの中で拒否する。"""
    identity = registry_forwarding.identity()
    if identity is None or not payload:
        return True
    if allow_claim and isinstance(claim := payload.get("resume_claim"), dict) and identity.owner in _recorded_processes(claim):
        return True
    instance = _recorded_server_instance(payload)
    if instance is not None and instance != identity.server_instance:
        return False
    processes = _recorded_processes(payload)
    return not processes or identity.owner is not None and processes[0].compatible_with(identity.owner)


def payload_server_instances(payload: dict[str, Any]) -> set[str]:
    instances = {turn.server_instance for turn in _recorded_interrupted_turns(payload) if turn.server_instance}
    if (instance := _recorded_server_instance(payload)) is not None:
        instances.add(instance)
    return instances


def _read_payload(path: pathlib.Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError, UnicodeError, json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _recorded_owner_root(payload: dict[str, Any]) -> str | None:
    value = payload.get("owner_root_session_id")
    return value if isinstance(value, str) and _SESSION_ID_PATTERN.fullmatch(value) else None


def _recorded_server_instance(payload: dict[str, Any]) -> str | None:
    value = payload.get("server_instance")
    return value if isinstance(value, str) and _SESSION_ID_PATTERN.fullmatch(value) else None


def _recorded_interrupted_turns(payload: dict[str, Any]) -> tuple[InterruptedTurn, ...]:
    entries = payload.get("interrupted_turns")
    if not isinstance(entries, list):
        return ()
    turns: list[InterruptedTurn] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        turn_seq = entry.get("turn_seq")
        reason = entry.get("reason")
        instance = entry.get("server_instance")
        at = entry.get("at")
        if (
            isinstance(turn_seq, int)
            and not isinstance(turn_seq, bool)
            and reason in _INTERRUPTION_MESSAGES
            and (instance is None or isinstance(instance, str) and _SESSION_ID_PATTERN.fullmatch(instance))
            and isinstance(at, str)
        ):
            turns.append(InterruptedTurn(turn_seq, reason, instance, at))
    return tuple(turns)


def _with_interrupted_turn(turns: tuple[InterruptedTurn, ...], turn: InterruptedTurn) -> tuple[InterruptedTurn, ...]:
    if any(existing.turn_seq == turn.turn_seq for existing in turns):
        return turns
    return (turn, *turns)[:INTERRUPTED_TURNS_LIMIT]


def _interrupted_turns_payload(turns: tuple[InterruptedTurn, ...]) -> list[dict[str, Any]]:
    return [dataclasses.asdict(turn) for turn in turns]


def _validate_session_id(session_id: str) -> None:
    if not isinstance(session_id, str) or not _SESSION_ID_PATTERN.fullmatch(session_id):
        raise ValueError(f"invalid session_id: {session_id}")


def _resume_info(payload: dict[str, Any]) -> ResumeInfo | None:
    status = payload.get("status")
    launch_kind = payload.get("launch_kind")
    startup_profile = payload.get("startup_profile")
    if (
        not isinstance(payload.get("engine"), str)
        or not isinstance(payload.get("cwd"), str)
        or payload.get("model") is not None
        and not isinstance(payload.get("model"), str)
        or payload.get("effort") is not None
        and not isinstance(payload.get("effort"), str)
        or payload.get("model_type") is not None
        and not isinstance(payload.get("model_type"), str)
        or launch_kind not in {"delegate", "explore", "shell", "write"}
        or startup_profile not in (None, "lightweight", "default", "full")
        or not isinstance(payload.get("turn_seq"), int)
        or isinstance(payload.get("turn_seq"), bool)
        or status not in _STATUSES
    ):
        return None
    return ResumeInfo(
        engine=payload["engine"],
        cwd=payload["cwd"],
        model=payload["model"],
        effort=payload["effort"],
        model_type=payload["model_type"],
        launch_kind=launch_kind,
        startup_profile=startup_profile,
        turn_seq=payload["turn_seq"],
        status=status,
        launch_info=LaunchInfo.from_record(payload),
        result_contract_recovery_consumed=payload.get("result_contract_recovery_consumed") is True,
        started_at=payload.get("started_at") if isinstance(payload.get("started_at"), str) else None,
        session_updated_at=payload.get("session_updated_at") if isinstance(payload.get("session_updated_at"), str) else None,
        turn_id=payload.get("turn_id") if isinstance(payload.get("turn_id"), str) and payload.get("turn_id") else None,
        fast_mode=payload.get("fast_mode")
        if payload["engine"] == "codex" and isinstance(payload.get("fast_mode"), bool)
        else None,
        codex_model_provider=payload.get("codex_model_provider")
        if payload["engine"] == "codex" and isinstance(payload.get("codex_model_provider"), str)
        else None,
        codex_subscription_provider=payload.get("codex_subscription_provider")
        if payload["engine"] == "codex" and isinstance(payload.get("codex_subscription_provider"), str)
        else None,
        codex_state_provider=payload.get("codex_state_provider")
        if payload["engine"] == "codex" and isinstance(payload.get("codex_state_provider"), str)
        else None,
        claude_api_env_file=payload.get("claude_api_env_file")
        if payload["engine"] == "claude" and isinstance(payload.get("claude_api_env_file"), str)
        else None,
        # `api`は種別を区別する前の版の保存値で、Claudeバックエンドが再開時に`credits`か`payg`へ読み替える。
        claude_connection=payload.get("claude_connection")
        if payload.get("claude_connection") in {"api", "credits", "payg", "subscription", "inherit"}
        else "api"
        if payload.get("claude_api_env_file")
        else None,
        claude_role=payload.get("claude_role")
        if payload.get("claude_role") in {"orchestrator", "delegate"}
        else "orchestrator"
        if payload.get("model_type") == "orchestrate"
        else "delegate",
    )

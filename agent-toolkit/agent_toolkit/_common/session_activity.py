from __future__ import annotations

import contextlib
import dataclasses
import datetime
import json
import math
import os
import pathlib
import time
from collections.abc import Collection, Mapping, Sequence
from typing import TYPE_CHECKING, Any

lazy import psutil

from agent_toolkit._common.atomic_file import atomic_write
from agent_toolkit._common.file_lock import acquire_lock, release_lock
from agent_toolkit._common.json_lines import split_lines
lazy from agent_toolkit._common import (
    delegated_session,
    host_homes,
    process_identity,
    session_launchers,
    session_parents,
    session_registry,
    state_paths,
)

if TYPE_CHECKING:
    from agent_toolkit._common.process_identity import ProcessIdentity


@dataclasses.dataclass(frozen=True)
class HostProcess:
    engine: str
    identity: ProcessIdentity


@dataclasses.dataclass(frozen=True)
class ActivitySnapshot:
    active_session_ids: frozenset[str] = frozenset()
    reasons: tuple[str, ...] = ()
    uncertain: bool = False

    @property
    def has_activity(self) -> bool:
        return bool(self.active_session_ids or self.reasons or self.uncertain)


def activity_directory(state_root: pathlib.Path | None = None) -> pathlib.Path:
    return (state_paths.state_dir() if state_root is None else state_root) / "session-activity"


def _valid_session(session_id: str) -> str:
    valid = session_launchers.valid_launcher(session_id)
    if valid is None:
        raise ValueError(f"会話IDとして使えない値です: {session_id!r}")
    return valid


def _engine(process: psutil.Process) -> str | None:
    names = {process.name().lower(), pathlib.Path(process.exe()).name.lower()}
    for engine in ("claude", "codex", "agy"):
        if names & {engine, f"{engine}.exe"}:
            return engine
    return None


def host_process() -> HostProcess | None:
    try:
        for process in process_identity.parents(psutil.Process()):
            if (engine := _engine(process)) is not None:
                return HostProcess(engine, process_identity.ProcessIdentity.capture(process))
    except psutil.Error, OSError:
        pass
    return None


def host_processes() -> tuple[HostProcess, ...]:
    found: list[HostProcess] = []
    username = psutil.Process().username()

    for listed in psutil.process_iter():
        try:
            process = psutil.Process(listed.pid)
        except psutil.NoSuchProcess:
            continue
        try:
            if process.username() != username or process.status() in (psutil.STATUS_ZOMBIE, psutil.STATUS_DEAD):
                continue
            if (engine := _engine(process)) is not None:
                found.append(HostProcess(engine, process_identity.ProcessIdentity.capture(process)))
        except psutil.NoSuchProcess, psutil.ZombieProcess:
            continue
        except psutil.AccessDenied:
            with contextlib.suppress(psutil.Error):
                name = process.name().lower().removesuffix(".exe")
                if name in {"claude", "codex", "agy"}:
                    found.append(HostProcess(name, process_identity.ProcessIdentity.capture(process)))
    return tuple(found)


def _read(path: pathlib.Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSONオブジェクトではありません: {path}")
    return value


def _identity(value: object) -> process_identity.ProcessIdentity | None:
    if not isinstance(value, dict):
        return None
    pid, started = value.get("pid"), value.get("create_time")
    if (
        not isinstance(pid, int)
        or isinstance(pid, bool)
        or pid <= 0
        or not isinstance(started, (int, float))
        or isinstance(started, bool)
        or not math.isfinite(started)
    ):
        return None
    return process_identity.ProcessIdentity(pid, float(started), value.get("start_id"))


def _valid_activity_record(record: Mapping[str, Any], session_id: str) -> bool:
    actors = record.get("actors")
    version = record.get("version")
    return (
        isinstance(version, int)
        and not isinstance(version, bool)
        and version == 1
        and record.get("session_id") == session_id
        and isinstance(record.get("engine"), str)
        and record.get("engine") in {"claude", "codex", "agy"}
        and isinstance(actors, dict)
        and all(
            session_launchers.valid_launcher(actor) is not None and isinstance(state, str) and state in {"active", "waiting"}
            for actor, state in actors.items()
        )
        and isinstance(record.get("background_active", False), bool)
    )


def _continuing(output: str, returncode: int) -> bool:
    if returncode == 2:
        return True
    try:
        response = json.loads(output) if output.strip() else {}
    except json.JSONDecodeError:
        return True
    if not isinstance(response, dict):
        return True
    if response.get("continue") is False:
        return False
    specific = response.get("hookSpecificOutput")
    context = specific.get("additionalContext") if isinstance(specific, dict) else None
    return response.get("decision") in {"block", "continue"} or bool(context)


def record_activity(
    payload: Mapping[str, Any],
    *,
    output: str = "",
    returncode: int = 0,
    process: HostProcess | None = None,
    state_root: pathlib.Path | None = None,
) -> None:
    """hookの実際の応答を反映して会話の活動を更新する。本文とツール入力は保存しない。

    入力形はClaude CodeとCodexの共通フィールドおよび正規化したagyのhookを使う。
    SubagentStart/Stopのsession_idは親会話で、agent_idはその会話内の主体である。
    """
    session_id = session_launchers.valid_launcher(payload.get("session_id"))
    event = payload.get("hook_event_name")
    if (
        session_id is None
        or not isinstance(event, str)
        or event
        not in {
            "SessionStart",
            "UserPromptSubmit",
            "PreInvocation",
            "PostInvocation",
            "PreToolUse",
            "PostToolUse",
            "PostToolUseFailure",
            "SubagentStart",
            "SubagentStop",
            "Stop",
            "StopFailure",
            "Interrupt",
            "SessionEnd",
        }
    ):
        return
    directory = activity_directory(state_root)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{session_id}.json"
    # ホスト祖先の探索は共有状態を読まないため、ロック取得前に済ませて別会話の記録を待たせない。
    host = process if process is not None or event == "SessionEnd" else host_process()
    # 会話JSONの交換・削除と並行する子hookは同じロックを使う。内容を持たないロックファイルは`activity.lock`の1件だけを保持する。
    with (directory / "activity.lock").open("a+", encoding="utf-8") as lock:
        acquire_lock(lock)
        try:
            if event == "SessionEnd":
                path.unlink(missing_ok=True)
                return
            try:
                record = _read(path)
            except OSError, ValueError, UnicodeError:
                record = {}
            prior_identity = _identity(record.get("process"))
            if host is not None and prior_identity != host.identity:
                record = {}
            actors = record.get("actors", {})
            actors = dict(actors) if isinstance(actors, dict) else {}
            actor = session_launchers.valid_launcher(payload.get("agent_id")) or "main"
            if event in {"Stop", "SubagentStop"}:
                if _continuing(output, returncode):
                    actors[actor] = "active"
                elif actor == "main":
                    actors[actor] = "waiting"
                else:
                    actors.pop(actor, None)
            elif event in {"Interrupt", "StopFailure"}:
                actors[actor] = "waiting"
            else:
                actors[actor] = "active"
            record.update(version=1, session_id=session_id, actors=actors, updated_at=time.time())
            if host is not None:
                record.update(engine=host.engine, process=dataclasses.asdict(host.identity))
            if record.get("engine") == "agy":
                # agyのPLANNER_RESPONSE/DONEはtoolの前にも現れるため、終了判定に使わない。
                # Stopの継続応答と、その後の入力・tool hookが示す実行状態を保持する。
                record["agy_turn_state"] = actors.get("main", "active")
            transcript = payload.get("transcript_path")
            if isinstance(transcript, str):
                record["transcript_path"] = transcript
            cwd = payload.get("cwd")
            if isinstance(cwd, str):
                record["cwd"] = cwd
            parent = delegated_session.owner_session_id(os.environ)
            if parent is not None and parent != session_id:
                record["parent_session_id"] = parent
            if event == "Stop":
                tasks = payload.get("background_tasks", ())
                record["background_active"] = bool(tasks)
            atomic_write(path, json.dumps(record, ensure_ascii=False) + "\n")
        finally:
            release_lock(lock)


def main(payload_text: str) -> int:
    payload = json.loads(payload_text)
    if isinstance(payload, dict):
        record_activity(payload)
    return 0


def _paths(directory: pathlib.Path, reasons: list[str]) -> list[pathlib.Path]:
    try:
        return sorted(entry for entry in directory.iterdir() if entry.suffix == ".json")
    except FileNotFoundError:
        return []
    except OSError as error:
        reasons.append(f"活動候補: {directory}を列挙できません: {error}")
        return []


def _excluded_processes(roots: set[int]) -> set[int]:
    excluded = set(roots)
    for pid in roots:
        with contextlib.suppress(psutil.Error, OSError):
            excluded.update(child.pid for child in psutil.Process(pid).children(recursive=True))
    return excluded


def _codex_parent(record: Mapping[str, Any]) -> str | None:
    path = record.get("transcript_path")
    if record.get("engine") != "codex" or not isinstance(path, str):
        return None
    try:
        with pathlib.Path(path).open(encoding="utf-8") as stream:
            first = json.loads(stream.readline())
        metadata = first.get("payload", {}) if isinstance(first, dict) else {}
        source = metadata.get("source", {}) if isinstance(metadata, dict) else {}
        subagent = source.get("subagent", {}) if isinstance(source, dict) else {}
        spawn = subagent.get("thread_spawn", {}) if isinstance(subagent, dict) else {}
        return session_launchers.valid_launcher(spawn.get("parent_thread_id")) if isinstance(spawn, dict) else None
    except OSError, UnicodeError, ValueError:
        return None


def _sources(records: Mapping[str, Mapping[str, Any]]) -> list[session_parents.ParentSource]:
    sources = [
        session_parents.ParentSource(
            session_id,
            str(record.get("engine", "")),
            session_id,
            None,
            _codex_parent(record),
        )
        for session_id, record in records.items()
    ]
    # 親自身の活動記録が無い場合も明示された親IDを解決できるよう、識別だけを補う。
    # launcherの環境値よりCodex自身の親threadを優先する規則は共通resolverへ残す。
    missing: dict[str, str] = {}
    for source in sources:
        parent = session_launchers.valid_launcher(records[source.path].get("parent_session_id"))
        if parent:
            missing.setdefault(parent, "")
        if source.codex_parent_thread_id:
            missing[source.codex_parent_thread_id] = "codex"
    return sources + [
        session_parents.ParentSource(parent, engine, parent, None, None)
        for parent, engine in missing.items()
        if parent not in records
    ]


def _native_sessions(process: HostProcess) -> tuple[dict[str, dict[str, Any]], str | None]:
    found: dict[str, dict[str, Any]] = {}
    try:
        if process.engine == "claude":
            path = host_homes.claude_config_dir() / "sessions" / f"{process.identity.pid}.json"
            native = _read(path)
            # Claude Code 2.1.296のprocStartはLinuxの/proc/statの開始tickを文字列化した値。
            if os.name == "posix" and pathlib.Path("/proc").is_dir():
                stat = pathlib.Path(f"/proc/{process.identity.pid}/stat").read_text(encoding="utf-8")
                if native.get("procStart") != stat.rsplit(")", 1)[1].split()[19]:
                    return {}, "登録されたプロセス起点が一致しません"
            else:
                return {}, "ホスト登録のプロセス起点を確認できません"
            session_id = session_launchers.valid_launcher(native.get("sessionId"))
            if session_id is not None:
                updated = native.get("statusUpdatedAt")
                found[session_id] = {
                    "engine": "claude",
                    "native_status": native.get("status"),
                    "native_updated_at": updated / 1000 if isinstance(updated, (int, float)) else 0,
                }
        else:
            for item in psutil.Process(process.identity.pid).open_files():
                path = pathlib.Path(item.path)
                if process.engine == "agy":
                    if (
                        path.name in {"transcript.jsonl", "transcript_full.jsonl"}
                        and path.parent.name == "logs"
                        and path.parent.parent.name == ".system_generated"
                        and path.parent.parent.parent.parent.name == "brain"
                    ):
                        session_id = session_launchers.valid_launcher(path.parent.parent.parent.name)
                        if session_id is not None:
                            found[session_id] = {"engine": "agy", "transcript_path": str(path)}
                elif path.name.startswith("rollout-") and path.suffix == ".jsonl":
                    with path.open(encoding="utf-8") as stream:
                        first = json.loads(stream.readline())
                    payload = first.get("payload", {}) if isinstance(first, dict) else {}
                    session_id = session_launchers.valid_launcher(payload.get("id")) if isinstance(payload, dict) else None
                    if session_id is not None:
                        found[session_id] = {"engine": "codex", "transcript_path": str(path)}
    except (OSError, psutil.Error, UnicodeError, ValueError) as error:
        return found, str(error)
    return found, None


def _turn_state(record: Mapping[str, Any], *, since: float | None = None) -> str | None:
    if record.get("engine") == "agy":
        state = record.get("agy_turn_state")
        return state if isinstance(state, str) and state in {"active", "waiting"} else None
    raw = record.get("transcript_path")
    if not isinstance(raw, str):
        return None
    try:
        with pathlib.Path(raw).open("rb") as stream:
            stream.seek(0, os.SEEK_END)
            size = stream.tell()
            stream.seek(max(0, size - 65536))
            tail = stream.read()
        lines = split_lines(tail)
        if size > 65536:
            lines = lines[1:]
        for line in reversed(lines):
            entry = json.loads(line)
            if not isinstance(entry, dict):
                continue
            if since is not None:
                stamp = entry.get("timestamp")
                if not isinstance(stamp, str):
                    continue
                try:
                    ended_at = datetime.datetime.fromisoformat(stamp.replace("Z", "+00:00"))
                    if ended_at.tzinfo is None or ended_at.timestamp() <= since:
                        continue
                except ValueError:
                    continue
            if record.get("engine") == "claude":
                message = entry.get("message")
                if entry.get("type") == "assistant" and isinstance(message, dict):
                    return "waiting" if message.get("stop_reason") in {"end_turn", "stop_sequence"} else "active"
                if entry.get("type") == "user":
                    return "active"
            payload = entry.get("payload")
            if entry.get("type") == "event_msg" and isinstance(payload, dict):
                if payload.get("type") in {"task_complete", "turn_aborted"}:
                    return "waiting"
                if payload.get("type") in {"task_started", "user_message"}:
                    return "active"
    except OSError, UnicodeError, ValueError:
        pass
    return None


@dataclasses.dataclass(frozen=True)
class _Scan:
    active: dict[str, str]
    """活動中または活動候補の会話IDから理由への対応。"""
    uncertain_ids: frozenset[str]
    general_reasons: tuple[str, ...]
    """会話へ対応付かない読取の失敗。"""
    links: dict[str, str]


def _scan(
    *,
    excluded_session_ids: set[str] | None,
    excluded_pids: set[int] | None,
    root: pathlib.Path,
    processes: Sequence[HostProcess] | None,
) -> _Scan:
    general_reasons: list[str] = []
    records: dict[str, dict[str, Any]] = {}
    unresolved: set[str] = set()
    for path in _paths(activity_directory(root), general_reasons):
        try:
            record = _read(path)
            identity = _identity(record.get("process"))
            if identity is not None and not identity.exists():
                continue
            if not _valid_activity_record(record, path.stem) or identity is None:
                unresolved.add(path.stem)
            records[path.stem] = record
        except (OSError, UnicodeError, ValueError) as error:
            records[path.stem] = {}
            unresolved.add(path.stem)
            records[path.stem]["read_error"] = str(error)
    for path in _paths(session_registry.registry_directory(root), general_reasons):
        session_id = session_launchers.valid_launcher(path.stem)
        if session_id is None:
            continue
        resolution = session_registry.resolve(session_id, state_root=root)
        if resolution.state in {session_registry.Resolution.TERMINAL, session_registry.Resolution.RELEASED}:
            records.pop(session_id, None)
            continue
        if session_id not in records:
            try:
                records[session_id] = _read(path)
            except OSError, UnicodeError, ValueError:
                records[session_id] = {}
            if resolution.state == session_registry.Resolution.UNREADABLE:
                unresolved.add(session_id)
            else:
                records[session_id]["registry_active"] = True
        if (parent := session_launchers.read_launcher(path)) is not None:
            records[session_id].setdefault("parent_session_id", parent)
    excluded_processes = _excluded_processes(excluded_pids or set())

    for host in host_processes() if processes is None else processes:
        if host.identity.pid in excluded_processes or not host.identity.exists():
            continue
        native, _warning = _native_sessions(host)
        for session_id, record in native.items():
            if session_id not in records:
                records[session_id] = record
                records[session_id]["process"] = dataclasses.asdict(host.identity)
            elif "native_status" in record:
                records[session_id]["native_status"] = record["native_status"]
                records[session_id]["native_updated_at"] = record["native_updated_at"]
    registry_launcher = session_launchers.launcher_reader(root)
    links = session_parents.resolve_parent_paths(
        _sources(records),
        delegated_ids=lambda _path, _engine: (),
        launcher_of=lambda session_id: (
            session_launchers.valid_launcher(records.get(session_id, {}).get("parent_session_id"))
            or registry_launcher(session_id)
        ),
    )
    excluded = session_parents.descendants(excluded_session_ids or set(), links)
    active: dict[str, str] = {}
    uncertain: set[str] = set()
    for session_id, record in records.items():
        identity = _identity(record.get("process"))
        if session_id in excluded or identity is not None and identity.pid in excluded_processes:
            continue
        actors = record.get("actors")
        if session_id in unresolved:
            uncertain.add(session_id)
            active[session_id] = f"活動候補: {session_id}の活動記録を判定できません"
        elif record.get("registry_active") or record.get("background_active"):
            active[session_id] = f"活動中: {session_id}の委譲・バックグラウンドタスクが稼働しています"
        elif isinstance(actors, dict) and any(value == "active" for value in actors.values()):
            updated = record.get("updated_at")
            main_only = not any(actor != "main" and state == "active" for actor, state in actors.items())

            if main_only and isinstance(updated, (int, float)):
                native_updated = record.get("native_updated_at", 0)
                native_idle = (
                    record.get("native_status") == "idle"
                    and isinstance(native_updated, (int, float))
                    and native_updated > updated
                )
                if native_idle or _turn_state(record, since=updated) == "waiting":
                    continue
            active[session_id] = f"活動中: {session_id}のターンまたは子エージェントが稼働しています"
        else:
            state = record.get("native_status") or _turn_state(record)
            if state not in {"idle", "waiting"}:
                if state is None:
                    uncertain.add(session_id)
                active[session_id] = f"活動候補: {session_id}の応答待ちを確認できません"
    return _Scan(active, frozenset(uncertain), tuple(general_reasons), links)


def scan_activity(
    *,
    excluded_session_ids: set[str] | None = None,
    excluded_pids: set[int] | None = None,
    state_root: pathlib.Path | None = None,
    processes: Sequence[HostProcess] | None = None,
) -> ActivitySnapshot:
    scan = _scan(
        excluded_session_ids=excluded_session_ids,
        excluded_pids=excluded_pids,
        root=state_paths.state_dir() if state_root is None else state_root,
        processes=processes,
    )
    return ActivitySnapshot(
        frozenset(scan.active),
        tuple(dict.fromkeys((*scan.general_reasons, *scan.active.values()))),
        bool(scan.general_reasons or scan.uncertain_ids),
    )


def root_activity(session_id: str, *, state_root: pathlib.Path | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {"status": "不明"}
    try:
        name = _valid_session(session_id)
        record = _read(activity_directory(state_root) / f"{name}.json")
        if not _valid_activity_record(record, name) or record.get("parent_session_id"):
            return result
        result["engine"] = record["engine"]
        identity = process_identity.ProcessIdentity.parse(record.get("process"))
        process = psutil.Process(identity.pid)
        if identity.matches(process) and process.status() not in {psutil.STATUS_ZOMBIE, psutil.STATUS_DEAD}:
            result["status"] = "稼働中"
        else:
            result["status"] = "終了"
    except psutil.NoSuchProcess, psutil.ZombieProcess:
        result["status"] = "終了"
    except OSError, UnicodeError, ValueError, psutil.Error:
        pass
    return result


def living_root_session_ids(*, state_root: pathlib.Path | None = None) -> frozenset[str]:
    found: set[str] = set()
    for path in _paths(activity_directory(state_root), []):
        try:
            record = _read(path)
        except OSError, UnicodeError, ValueError:
            continue
        identity = _identity(record.get("process"))
        if (
            session_launchers.valid_launcher(path.stem) is not None
            and _valid_activity_record(record, path.stem)
            and not record.get("parent_session_id")
            and identity is not None
            and identity.exists()
        ):
            found.add(path.stem)
    return frozenset(found)


def sweep_exited_records(*, state_root: pathlib.Path | None = None) -> int:
    """記録したホストプロセスの終了が確定した会話の活動JSONを回収し、削除した件数を返す。

    SessionEndが届かずに終わった会話の記録は活動の判定に使われないまま残り、一覧のたびに全件が読まれる
    （2026-11-19の実環境で3,276件中3,263件）。同じ会話を別のプロセスで再開すると最初のhookが記録を作り直すため、
    終了したプロセスの記録は再開にも使われない。プロセスを記録していない記録と読めない記録は判定の根拠として残す。
    """
    directory = activity_directory(state_root)
    removed = 0
    for path in _paths(directory, []):
        if not _exited(path):
            continue
        # 判定から削除までに同じ会話が再開して記録を作り直していないかを、記録を書く側と同じロックの下で確かめ直す。
        with (directory / "activity.lock").open("a+", encoding="utf-8") as lock:
            acquire_lock(lock)
            try:
                if _exited(path):
                    path.unlink(missing_ok=True)
                    removed += 1
            finally:
                release_lock(lock)
    return removed


def _exited(path: pathlib.Path) -> bool:
    try:
        identity = _identity(_read(path).get("process"))
    except OSError, UnicodeError, ValueError:
        return False
    return identity is not None and not identity.exists()


def review_activity(session_ids: Collection[str], *, state_root: pathlib.Path | None = None) -> ActivitySnapshot:
    scan = _scan(
        excluded_session_ids=None,
        excluded_pids=None,
        root=state_paths.state_dir() if state_root is None else state_root,
        processes=None,
    )
    members = session_parents.descendants(set(session_ids), scan.links)
    active = {session_id: reason for session_id, reason in scan.active.items() if session_id in members}
    return ActivitySnapshot(
        frozenset(active),
        tuple((*scan.general_reasons, *active.values())),
        bool(scan.general_reasons or scan.uncertain_ids & members),
    )

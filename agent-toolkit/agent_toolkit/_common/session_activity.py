"""ホスト全体の活動をhook、実プロセス、セッション記録と登録簿から取得する。

開始猶予の期限は呼び出し元が持つ。ここでは未登録・読取不能を活動候補として返し、
無活動へ読み替えない。hookの記録は会話ごとの最新状態だけを保持する。
"""

from __future__ import annotations

import contextlib
import dataclasses
import datetime
import json
import math
import os
import pathlib
import time
from collections.abc import Mapping, Sequence
from typing import Any

import psutil

from agent_toolkit._common import (
    delegated_session,
    host_homes,
    session_launchers,
    session_parents,
    session_registry,
    state_paths,
)
from agent_toolkit._common.atomic_file import atomic_write
from agent_toolkit._common.file_lock import acquire_lock, release_lock


@dataclasses.dataclass(frozen=True)
class HostProcess:
    """生存ホストの実行系とPID・起動時刻。"""

    engine: str
    identity: session_registry.ProcessIdentity


@dataclasses.dataclass(frozen=True)
class ActivitySnapshot:
    """活動候補と、その判断を端末・ログへ渡すための根拠。"""

    active_session_ids: frozenset[str] = frozenset()
    reasons: tuple[str, ...] = ()
    uncertain: bool = False

    @property
    def has_activity(self) -> bool:
        """確定した活動または判定不能の活動候補があれば真を返す。"""
        return bool(self.active_session_ids or self.reasons or self.uncertain)


def activity_directory(state_root: pathlib.Path | None = None) -> pathlib.Path:
    """活動JSONを置く監視先を返す。"""
    return (state_paths.state_dir() if state_root is None else state_root) / "session-activity"


def _engine(process: psutil.Process) -> str | None:
    names = {process.name().lower(), pathlib.Path(process.exe()).name.lower()}
    for engine in ("claude", "codex"):
        if names & {engine, f"{engine}.exe"}:
            return engine
    return None


def host_process() -> HostProcess | None:
    """hookを起動した最も近いホスト祖先を返す。hook自身のPythonを記録しない。"""
    try:
        for process in psutil.Process().parents():
            if (engine := _engine(process)) is not None:
                return HostProcess(engine, session_registry.ProcessIdentity(process.pid, process.create_time()))
    except psutil.Error, OSError:
        pass
    return None


def host_processes() -> tuple[HostProcess, ...]:
    """現在のOSアカウントが動かす両ホストを、名前の完全一致で列挙する。"""
    found: list[HostProcess] = []
    username = psutil.Process().username()
    for process in psutil.process_iter():
        try:
            if process.username() != username or process.status() == psutil.STATUS_ZOMBIE:
                continue
            if (engine := _engine(process)) is not None:
                found.append(HostProcess(engine, session_registry.ProcessIdentity(process.pid, process.create_time())))
        except psutil.NoSuchProcess, psutil.ZombieProcess:
            continue
        except psutil.AccessDenied:
            # 名前まで取得できるホストは、実行ファイルを読めなくても未登録候補へ残す。
            with contextlib.suppress(psutil.Error):
                name = process.name().lower().removesuffix(".exe")
                if name in {"claude", "codex"}:
                    found.append(HostProcess(name, session_registry.ProcessIdentity(process.pid, process.create_time())))
    return tuple(found)


def _read(path: pathlib.Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSONオブジェクトではありません: {path}")
    return value


def _identity(value: object) -> session_registry.ProcessIdentity | None:
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
    return session_registry.ProcessIdentity(pid, float(started))


def _valid_activity_record(record: Mapping[str, Any], session_id: str) -> bool:
    actors = record.get("actors")
    version = record.get("version")
    return (
        isinstance(version, int)
        and not isinstance(version, bool)
        and version == 1
        and record.get("session_id") == session_id
        and isinstance(record.get("engine"), str)
        and record.get("engine") in {"claude", "codex"}
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
    return response.get("decision") == "block" or bool(context)


def record_activity(
    payload: Mapping[str, Any],
    *,
    output: str = "",
    returncode: int = 0,
    process: HostProcess | None = None,
    state_root: pathlib.Path | None = None,
) -> None:
    """hookの実際の応答を反映して会話の活動を更新する。本文とツール入力は保存しない。

    入力形はClaude CodeとCodexの公式hookの共通フィールドを使う。
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
    # 会話JSONの交換・削除と並行する子hookは同じロックを使う。空ロックは固定1件だけ保持する。
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
            host = host_process() if process is None else process
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
            transcript = payload.get("transcript_path")
            if isinstance(transcript, str):
                record["transcript_path"] = transcript
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
    """記録だけを行うhookの入力を受理し、ホストへ判定を返さず終了する。"""
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
    """hook未登録ホストの会話IDと状態を読む。ホスト固有形式はこの境界へ閉じる。"""
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
                if path.name.startswith("rollout-") and path.suffix == ".jsonl":
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
    """セッション記録の末尾から実ターンの終了を読む。見つからない場合は未判定にする。"""
    raw = record.get("transcript_path")
    if not isinstance(raw, str):
        return None
    try:
        with pathlib.Path(raw).open("rb") as stream:
            stream.seek(0, os.SEEK_END)
            size = stream.tell()
            stream.seek(max(0, size - 65536))
            tail = stream.read()
        lines = tail.splitlines()
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


def scan_activity(
    *,
    excluded_session_ids: set[str] | None = None,
    excluded_pids: set[int] | None = None,
    state_root: pathlib.Path | None = None,
    processes: Sequence[HostProcess] | None = None,
) -> ActivitySnapshot:
    """別会話の活動と未判定の根拠を返す。除外は指定した会話・PIDの子孫だけに作用する。

    `state_root`と`processes`は隔離した記録と実プロセスを組み合わせる呼び出しにも使える。
    読取は状態を変更せず、死亡・PID再利用の記録は活動から失効させる。
    """
    root = state_paths.state_dir() if state_root is None else state_root
    reasons: list[str] = []
    records: dict[str, dict[str, Any]] = {}
    unresolved: set[str] = set()
    known_pids: set[int] = set()
    for path in _paths(activity_directory(root), reasons):
        try:
            record = _read(path)
            identity = _identity(record.get("process"))
            if identity is not None and not identity.exists():
                continue
            if not _valid_activity_record(record, path.stem) or identity is None:
                unresolved.add(path.stem)
            else:
                known_pids.add(identity.pid)
            records[path.stem] = record
        except (OSError, UnicodeError, ValueError) as error:
            records[path.stem] = {}
            unresolved.add(path.stem)
            records[path.stem]["read_error"] = str(error)
    registry_paths = _paths(session_registry.registry_directory(root), reasons)
    for path in registry_paths:
        session_id = session_launchers.valid_launcher(path.stem)
        if session_id is None:
            continue
        resolution = session_registry.resolve(session_id, state_root=root)
        if resolution.state in {session_registry.Resolution.TERMINAL, session_registry.Resolution.RELEASED}:
            records.pop(session_id, None)
            continue
        try:
            registered = _read(path)
        except OSError, UnicodeError, ValueError:
            registered = {}
        identities = registered.get("processes", ())
        if isinstance(identities, list):
            known_pids.update(
                identity.pid for item in identities if (identity := _identity(item)) is not None and identity.exists()
            )
        if session_id not in records:
            records[session_id] = registered
            if resolution.state == session_registry.Resolution.UNREADABLE:
                unresolved.add(session_id)
            else:
                records[session_id]["registry_active"] = True
        if (parent := session_launchers.read_launcher(path)) is not None:
            records[session_id].setdefault("parent_session_id", parent)
    excluded_processes = _excluded_processes(excluded_pids or set())
    hosts = tuple(host_processes() if processes is None else processes)
    native_by_pid: dict[int, set[str]] = {}
    for host in hosts:
        if host.identity.pid in excluded_processes or not host.identity.exists():
            continue
        native, warning = _native_sessions(host)
        native_by_pid[host.identity.pid] = set(native)
        for session_id, record in native.items():
            if session_id not in records:
                records[session_id] = record
                records[session_id]["process"] = dataclasses.asdict(host.identity)
            elif "native_status" in record:
                records[session_id]["native_status"] = record["native_status"]
                records[session_id]["native_updated_at"] = record["native_updated_at"]
        if warning and host.identity.pid not in known_pids and not native:
            # 除外対象の解決後に、このPIDに対応する候補だけへ診断を載せる。
            native_by_pid[host.identity.pid] = set()
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
    active: set[str] = set()
    uncertain = bool(reasons)
    for session_id, record in records.items():
        identity = _identity(record.get("process"))
        if session_id in excluded or identity is not None and identity.pid in excluded_processes:
            continue
        actors = record.get("actors")
        if session_id in unresolved:
            uncertain = True
            active.add(session_id)
            reasons.append(f"活動候補: {session_id}の活動記録を判定できません")
        elif record.get("registry_active") or record.get("background_active"):
            active.add(session_id)
            reasons.append(f"活動中: {session_id}の委譲・バックグラウンドタスクが稼働しています")
        elif isinstance(actors, dict) and any(value == "active" for value in actors.values()):
            updated = record.get("updated_at")
            main_only = not any(actor != "main" and state == "active" for actor, state in actors.items())
            # Stop hookが欠けても、最後の入力より後の実ターン終了があれば活動から外せる。
            if main_only and isinstance(updated, (int, float)):
                native_updated = record.get("native_updated_at", 0)
                native_idle = (
                    record.get("native_status") == "idle"
                    and isinstance(native_updated, (int, float))
                    and native_updated > updated
                )
                if native_idle or _turn_state(record, since=updated) == "waiting":
                    continue
            active.add(session_id)
            reasons.append(f"活動中: {session_id}のターンまたは子エージェントが稼働しています")
        else:
            # Stop自身の発火を終了証拠にせず、実ターンかホストのidle状態を確かめる。
            state = record.get("native_status") or _turn_state(record)
            if state not in {"idle", "waiting"}:
                uncertain |= state is None
                active.add(session_id)
                reasons.append(f"活動候補: {session_id}の応答待ちを確認できません")
    for host in hosts:
        pid = host.identity.pid
        if pid in excluded_processes or pid in known_pids or not host.identity.exists():
            continue
        session_ids = native_by_pid.get(pid, set())
        if not session_ids:
            uncertain = True
            reasons.append(f"活動候補: 未登録の{host.engine} PID {pid}が生存しています")
    return ActivitySnapshot(frozenset(active), tuple(dict.fromkeys(reasons)), uncertain)

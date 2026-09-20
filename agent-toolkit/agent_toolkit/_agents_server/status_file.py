"""agents_serverのsession状態をClaude Codeのstatusline向けに出力する。

Claude backendの委譲先は所有sessionと自身のClaude Code sessionを持つ。
この対応はClaude Code 2.1.261及びclaude-agent-sdk 0.2系で確認した。
Codex backendの委譲先自身のシェルは、所有sessionと自身のCodex threadを持つ。
2026年9月7日にCodex CLI 0.153.4の`start_shell`で起動したシェルに
`CODEX_THREAD_ID`が存在することを確認した。

Codex CLIが直接起動するMCPサーバープロセスへは、Codex App Server自身の環境が
継承されない。Codex CLI 0.153.4で2026年9月9日に確認した。`thread/start`の
`config.mcp_servers.agents_server`へ完全な定義を渡す場合だけ、`env`の識別子が
当該プロセスへ届く。ホスト、CLI又はSDKを更新した時点では、同じ起動形で
MCPサーバーの環境変数と起動通知を確認する。

上り通知の配送媒体は本モジュールが定める共有状態ディレクトリとする。Codexの委譲先にはagents_server系のMCPツールもフックの発火機構も公開されず、Claudeの委譲先へ公開されるagents_server系のMCPツールは委譲元のsession登録簿を共有しないため、engineに依存しない媒体が他に無い。2026年9月6日に両engineの委譲先を1件ずつ起動して実測した。この前提が崩れた場合は、片方のengineの委譲先から送った通知が委譲元へ届かない事象として現れる。
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import datetime
import json
import logging
import pathlib
import re
import shutil
from collections.abc import Callable, Mapping
from typing import Any

from agent_toolkit._agents_server import compaction_metrics, session_registry
from agent_toolkit._agents_server.state import (
    RESULT_RETENTION_SECONDS,
    TERMINAL_STATUSES,
    SessionResumeState,
    SessionState,
    has_uncollected_result,
    terminal_result_payload,
)
from agent_toolkit._atk import config as _atk_config
from agent_toolkit._common.atomic_file import atomic_write
from agent_toolkit._common.file_lock import acquire_lock, release_lock

_LOG = logging.getLogger("agent-toolkit.agents-server.status-file")

_SESSION_ID_PATTERN = re.compile(r"^[0-9A-Za-z_-]+$")
HEARTBEAT_INTERVAL_SECONDS = 30
HEARTBEAT_EXPIRY_SECONDS = 120
STALE_SHARED_STATE_SECONDS = 7 * 24 * 60 * 60


@dataclasses.dataclass(frozen=True)
class StatusFileIdentity:
    """状態ファイルのルートと書込主体を表す。"""

    root_session_id: str
    file_name: str
    host_session_id: str | None


def resolve_root_session_id(environment: Mapping[str, str]) -> str | None:
    """環境変数から読取対象のルートsessionを解決する。"""
    owner = environment.get("AGENT_TOOLKIT_OWNER_SESSION") or environment.get("CLAUDE_CODE_SESSION_ID")
    return owner if owner is not None and valid_session_id(owner) else None


def resolve_status_file_identity(environment: Mapping[str, str]) -> StatusFileIdentity | None:
    """環境変数から状態ファイルの書込主体を解決し、識別できない場合は`None`を返す。"""
    owner = resolve_root_session_id(environment)
    if owner is None:
        return None

    host_session_id: str | None = None
    if environment.get("AGENT_TOOLKIT_DELEGATED_SESSION"):
        host_session_id = environment.get("CLAUDE_CODE_SESSION_ID")
    elif environment.get("CODEX_THREAD_ID"):
        host_session_id = environment.get("CODEX_THREAD_ID")
    elif environment.get("AGENT_TOOLKIT_STATUS_HOST_SESSION"):
        host_session_id = environment.get("AGENT_TOOLKIT_STATUS_HOST_SESSION")
    if host_session_id is not None and not valid_session_id(host_session_id):
        return None
    if host_session_id is None and environment.get("AGENT_TOOLKIT_OWNER_SESSION"):
        return None

    file_name = "root.json" if host_session_id is None else f"{host_session_id}.json"
    return StatusFileIdentity(owner, file_name, host_session_id)


def status_directory(root_session_id: str, state_root: pathlib.Path | None = None) -> pathlib.Path:
    """ルートsessionの状態ファイルディレクトリを返す。"""
    root = _atk_config.state_dir() if state_root is None else state_root
    return root / "agents-server" / root_session_id


def list_status_files(root_session_id: str, state_root: pathlib.Path | None = None) -> list[pathlib.Path]:
    """書込主体ごとの状態ファイルを絶対パスの安定順で返す。

    書込主体ごとに`root.json`と`<host_session_id>.json`へ分かれるため、
    読取主体は単一のファイル名を組み立てない。ファイル名の規則の正本は
    `resolve_status_file_identity`である。
    """
    directory = status_directory(root_session_id, state_root)
    try:
        paths = [path.absolute() for path in directory.iterdir() if path.suffix == ".json" and path.is_file()]
    except OSError:
        return []
    return sorted(paths)


def list_root_session_ids(state_root: pathlib.Path | None = None) -> list[str]:
    """共有状態に存在する有効なルートsession識別子を安定順で返す。"""
    root = _atk_config.state_dir() if state_root is None else state_root
    base = root / "agents-server"
    try:
        identifiers = [path.name for path in base.iterdir() if path.is_dir() and valid_session_id(path.name)]
    except OSError:
        return []
    return sorted(identifiers)


def sweep_stale_shared_state(
    *,
    keep_root_session_id: str | None,
    state_root: pathlib.Path | None = None,
    now: float | None = None,
) -> None:
    """7日を超えて更新されていない共有状態を個別失敗で停止せず回収する。"""
    root = _atk_config.state_dir() if state_root is None else state_root
    cutoff = datetime.datetime.now(datetime.UTC).timestamp() if now is None else now
    cutoff -= STALE_SHARED_STATE_SECONDS
    base = root / "agents-server"
    try:
        entries = tuple(base.iterdir())
    except OSError:
        return
    reserved = {"aliases", "sessions", "compaction"}
    for directory in entries:
        if (
            not directory.is_dir()
            or directory.name in reserved
            or directory.name == keep_root_session_id
            or not valid_session_id(directory.name)
        ):
            continue
        try:
            files = tuple(path for path in directory.rglob("*") if path.is_file())
            latest = max((path.stat().st_mtime for path in files), default=directory.stat().st_mtime)
            if latest < cutoff:
                shutil.rmtree(directory)
        except OSError as exc:
            _LOG.warning("stale root状態を回収できませんでした: path=%s error=%s", directory, exc)
    _sweep_stale_files(session_registry.registry_directory(root), cutoff)
    _sweep_stale_compaction(compaction_metrics.record_directory(root), cutoff)


def _sweep_stale_files(directory: pathlib.Path, cutoff: float) -> None:
    """ディレクトリ直下の期限切れファイルを個別失敗で停止せず削除する。"""
    try:
        paths = tuple(directory.iterdir())
    except OSError:
        return
    for path in paths:
        try:
            if path.is_file() and path.stat().st_mtime < cutoff:
                path.unlink()
        except OSError as exc:
            _LOG.warning("期限切れ共有状態を回収できませんでした: path=%s error=%s", path, exc)


def _sweep_stale_compaction(directory: pathlib.Path, cutoff: float) -> None:
    """期限切れJSONLと対応するlockを対として削除する。"""
    try:
        paths = tuple(directory.iterdir())
    except OSError:
        return
    records = {
        path.with_name(path.name.removesuffix(".lock")) if path.name.endswith(".jsonl.lock") else path
        for path in paths
        if path.name.endswith((".jsonl", ".jsonl.lock"))
    }
    for record in records:
        lock = record.with_name(f"{record.name}.lock")
        try:
            members = [path for path in (record, lock) if path.exists()]
            if members and max(path.stat().st_mtime for path in members) < cutoff:
                for path in members:
                    path.unlink(missing_ok=True)
        except OSError as exc:
            _LOG.warning("期限切れコンパクション記録を回収できませんでした: path=%s error=%s", record, exc)


def find_root_session_id_for_session(session_id: str, state_root: pathlib.Path | None = None) -> str | None:
    """共有状態から指定sessionを保持する一意なルートsession識別子を返す。"""
    if not valid_session_id(session_id):
        return None
    matches: list[str] = []
    for root_session_id in list_root_session_ids(state_root):
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


UNAVAILABLE_CANDIDATES_RETENTION_SECONDS = 1800.0
"""可用性を理由に除外した候補を、最終除外時刻から保持し続ける秒数。

上限の回復を検知する遅れと、枯渇した候補を先頭から試す待機の発生頻度との兼ね合いで選んだ値である。
"""


def unavailable_candidates_path(state_root: pathlib.Path | None = None) -> pathlib.Path:
    """可用性を理由に除外した候補の記録ファイルを返す。

    `list_root_session_ids`は`agents-server`直下のディレクトリをルートsession識別子として列挙するため、
    当該階層へはディレクトリではなく単一のファイルとして置く。
    """
    root = _atk_config.state_dir() if state_root is None else state_root
    return root / "agents-server" / "unavailable-candidates.json"


def _read_unavailable_entries(path: pathlib.Path) -> list[dict[str, Any]]:
    """記録ファイルのうち、書式に適合する項目だけを返す。"""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, UnicodeError, json.JSONDecodeError):
        return []
    if not isinstance(payload, dict) or payload.get("version") != 1:
        return []
    entries = payload.get("entries")
    if not isinstance(entries, list):
        return []
    return [entry for entry in entries if isinstance(entry, dict)]


def _unavailable_entry_is_live(entry: dict[str, Any], now: datetime.datetime) -> bool:
    """最終除外時刻から保持期間内の項目かを返す。"""
    try:
        excluded_at = datetime.datetime.fromisoformat(str(entry.get("excluded_at")))
    except (TypeError, ValueError):
        return False
    if excluded_at.tzinfo is None:
        return False
    return (now - excluded_at).total_seconds() < UNAVAILABLE_CANDIDATES_RETENTION_SECONDS


def load_unavailable_candidates(
    model_type: str,
    launch_kind: str,
    *,
    now: datetime.datetime,
    state_root: pathlib.Path | None = None,
) -> dict[tuple[str, str | None, str | None], str]:
    """当該起動条件で除外中の候補を、候補ごとの除外理由とともに返す。

    保持期間を過ぎた項目は返さない。呼び出し元は残った候補だけを除外集合として扱う。
    """
    result: dict[tuple[str, str | None, str | None], str] = {}
    for entry in _read_unavailable_entries(unavailable_candidates_path(state_root)):
        if entry.get("model_type") != model_type or entry.get("launch_kind") != launch_kind:
            continue
        if not _unavailable_entry_is_live(entry, now):
            continue
        engine = entry.get("engine")
        if not isinstance(engine, str):
            continue
        model = entry.get("model")
        effort = entry.get("effort")
        result[(engine, model if isinstance(model, str) else None, effort if isinstance(effort, str) else None)] = str(
            entry.get("reason", "")
        )
    return result


def _update_unavailable_candidates(
    path: pathlib.Path,
    *,
    now: datetime.datetime,
    replace: Callable[[list[dict[str, Any]]], list[dict[str, Any]]],
) -> None:
    """記録ファイルを排他区間で読み書きし、保持期間を過ぎた項目を取り除く。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.parent / ".unavailable-candidates.lock"
    with lock_path.open("a+b") as lock_file:
        acquire_lock(lock_file, blocking=True)
        try:
            entries = [entry for entry in _read_unavailable_entries(path) if _unavailable_entry_is_live(entry, now)]
            atomic_write(
                path,
                json.dumps({"version": 1, "entries": replace(entries)}, ensure_ascii=False) + "\n",
            )
        finally:
            release_lock(lock_file)


def record_unavailable_candidate(
    model_type: str,
    launch_kind: str,
    candidate: tuple[str, str | None, str | None],
    reason: str,
    *,
    now: datetime.datetime,
    state_root: pathlib.Path | None = None,
) -> None:
    """可用性を理由に除外した候補の最終除外時刻を記録する。"""
    engine, model, effort = candidate

    def replace(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
        kept = [entry for entry in entries if not _matches_candidate(entry, model_type, launch_kind, engine, model, effort)]
        kept.append(
            {
                "model_type": model_type,
                "launch_kind": launch_kind,
                "engine": engine,
                "model": model,
                "effort": effort,
                "reason": reason,
                "excluded_at": now.isoformat(),
            }
        )
        return kept

    _update_unavailable_candidates(unavailable_candidates_path(state_root), now=now, replace=replace)


def clear_unavailable_candidate(
    model_type: str,
    launch_kind: str,
    candidate: tuple[str, str | None, str | None],
    *,
    now: datetime.datetime,
    state_root: pathlib.Path | None = None,
) -> None:
    """起動が成立した候補の除外記録を取り除く。

    当該候補の記録が無い場合は書き込まない。起動のたびに記録ファイルを作成しないためである。
    """
    engine, model, effort = candidate
    if candidate not in load_unavailable_candidates(model_type, launch_kind, now=now, state_root=state_root):
        return

    def replace(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [entry for entry in entries if not _matches_candidate(entry, model_type, launch_kind, engine, model, effort)]

    _update_unavailable_candidates(unavailable_candidates_path(state_root), now=now, replace=replace)


def _matches_candidate(
    entry: dict[str, Any],
    model_type: str,
    launch_kind: str,
    engine: str,
    model: str | None,
    effort: str | None,
) -> bool:
    """記録した項目が当該起動条件と候補の組に一致するかを返す。"""
    return (
        entry.get("model_type") == model_type
        and entry.get("launch_kind") == launch_kind
        and entry.get("engine") == engine
        and entry.get("model") == model
        and entry.get("effort") == effort
    )


def aliases_directory(state_root: pathlib.Path | None = None) -> pathlib.Path:
    """現行session識別子からルートsession識別子を引く索引ディレクトリを返す。"""
    root = _atk_config.state_dir() if state_root is None else state_root
    return root / "agents-server" / "aliases"


def resolve_conversation_root_session_id(environment: Mapping[str, str], state_root: pathlib.Path | None = None) -> str | None:
    """現行会話のsession識別子を索引経由でルートsession識別子へ解決する。"""
    current_session_id = resolve_root_session_id(environment)
    if current_session_id is None:
        return None
    alias_path = aliases_directory(state_root) / f"{current_session_id}.json"
    try:
        payload = json.loads(alias_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, UnicodeError, json.JSONDecodeError):
        return current_session_id
    root_session_id = payload.get("root_session_id") if isinstance(payload, dict) else None
    if (
        isinstance(payload, dict)
        and payload.get("version") == 1
        and isinstance(root_session_id, str)
        and valid_session_id(root_session_id)
        and status_directory(root_session_id, state_root).is_dir()
    ):
        return root_session_id
    return current_session_id


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


def valid_session_id(session_id: str) -> bool:
    """session識別子が状態ファイル名へ使用できる形式かを返す。"""
    return bool(session_id and _SESSION_ID_PATTERN.fullmatch(session_id))


def results_directory(root_session_id: str, state_root: pathlib.Path | None = None) -> pathlib.Path:
    """ルートsessionの終端結果ディレクトリを返す。"""
    return status_directory(root_session_id, state_root) / "results"


def take_result(
    root_session_id: str,
    session_id: str,
    owner_status_file: str,
    *,
    collector: str,
    state_root: pathlib.Path | None = None,
) -> tuple[dict[str, Any] | None, str | None]:
    """所有者を照合し、終端結果を1つの排他区間で読み取って回収する。"""
    if not valid_session_id(session_id):
        raise ValueError(f"invalid session_id: {session_id}")
    directory = results_directory(root_session_id, state_root)
    directory.mkdir(parents=True, exist_ok=True)
    lock_path = directory / f".{session_id}.claim.lock"
    with lock_path.open("a+b") as lock_file:
        acquire_lock(lock_file, blocking=True)
        try:
            path = directory / f"{session_id}.json"
            try:
                payload: Any = json.loads(path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                return None, None
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                return None, str(exc)
            if not isinstance(payload, dict):
                return None, "最上位が辞書ではありません"
            recorded_owner = payload.get("owner_status_file")
            if recorded_owner != owner_status_file and not (recorded_owner is None and owner_status_file == "root.json"):
                return None, None
            path.unlink()
            _LOG.info(
                "result_deleted session_id=%s writer=%s collector=%s",
                session_id,
                owner_status_file,
                collector,
            )
            payload.pop("owner_status_file", None)
            return payload, None
        finally:
            release_lock(lock_file)


def wait_targets_directory(
    root_session_id: str,
    owner_status_file: str,
    state_root: pathlib.Path | None = None,
) -> pathlib.Path:
    """書込主体ごとの待機対象登録簿のディレクトリを返す。"""
    return status_directory(root_session_id, state_root) / "wait-targets" / owner_status_file


def read_wait_targets(
    root_session_id: str,
    owner_status_file: str,
    state_root: pathlib.Path | None = None,
) -> tuple[set[str], str | None]:
    """未回収の待機対象を返し、破損した登録は解放して理由を返す。"""
    directory = wait_targets_directory(root_session_id, owner_status_file, state_root)
    try:
        paths = tuple(directory.iterdir())
    except FileNotFoundError:
        return set(), None
    except OSError as exc:
        return set(), str(exc)
    retained: set[str] = set()
    for path in paths:
        if path.suffix != ".json" or not path.is_file() or not valid_session_id(path.stem):
            continue
        try:
            payload: Any = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            with contextlib.suppress(OSError):
                path.unlink(missing_ok=True)
            return set(), f"{path}: {exc}"
        if not isinstance(payload, dict) or payload != {"version": 1, "session_id": path.stem}:
            with contextlib.suppress(OSError):
                path.unlink(missing_ok=True)
            return set(), f"{path}: 必須項目が不正です"
        retained.add(path.stem)
    return retained, None


def retain_wait_targets(
    root_session_id: str,
    owner_status_file: str,
    session_ids: list[str],
    state_root: pathlib.Path | None = None,
) -> None:
    """待機対象を再発行後も復元できるよう書込主体の登録簿へ残す。"""
    if not session_ids:
        return
    directory = wait_targets_directory(root_session_id, owner_status_file, state_root)
    for session_id in session_ids:
        if not valid_session_id(session_id):
            raise ValueError(f"invalid session_id: {session_id}")
        atomic_write(
            directory / f"{session_id}.json",
            json.dumps({"version": 1, "session_id": session_id}, ensure_ascii=False) + "\n",
        )


def release_wait_target(
    root_session_id: str,
    owner_status_file: str,
    session_id: str,
    state_root: pathlib.Path | None = None,
) -> None:
    """回収又は明示破棄したsessionを待機対象登録簿から除く。"""
    if not valid_session_id(session_id):
        raise ValueError(f"invalid session_id: {session_id}")
    directory = wait_targets_directory(root_session_id, owner_status_file, state_root)
    (directory / f"{session_id}.json").unlink(missing_ok=True)
    if directory.exists() and not any(directory.iterdir()):
        with contextlib.suppress(OSError):
            directory.rmdir()


def notices_directory(root_session_id: str, state_root: pathlib.Path | None = None) -> pathlib.Path:
    """ルートsession宛ての未回収通知ディレクトリを返す。"""
    return status_directory(root_session_id, state_root) / "notices"


def hosts_directory(root_session_id: str, state_root: pathlib.Path | None = None) -> pathlib.Path:
    """書込主体から起動元threadへの索引ディレクトリを返す。"""
    return status_directory(root_session_id, state_root) / "hosts"


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
        return identity
    except OSError as error:
        raise ValueError(f"書込主体索引を読めません: {error}") from error

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
        return identity
    if len(writers) != 1:
        raise ValueError(
            "起動元sessionに対応する書込主体を一意に解決できません: "
            f"host_session_id={identity.host_session_id}, writers={','.join(sorted(writers))}"
        )
    writer_session_id = writers[0]
    return StatusFileIdentity(identity.root_session_id, f"{writer_session_id}.json", writer_session_id)


def write_host_alias(
    root_session_id: str,
    writer_session_id: str,
    host_session_id: str,
    state_root: pathlib.Path | None = None,
) -> None:
    """書込主体を起動元threadへ対応付ける索引を書く。"""
    if not all(valid_session_id(value) for value in (root_session_id, writer_session_id, host_session_id)):
        raise ValueError("invalid session_id")
    payload = {"version": 1, "host_session_id": host_session_id}
    atomic_write(
        hosts_directory(root_session_id, state_root) / f"{writer_session_id}.json",
        json.dumps(payload, ensure_ascii=False) + "\n",
    )


def take_notices(
    root_session_id: str,
    session_id: str,
    state_root: pathlib.Path | None = None,
) -> list[dict[str, str]]:
    """待機対象sessionの正常な通知を回収し、送信時刻順に返す。"""
    directory = notices_directory(root_session_id, state_root)
    try:
        paths = tuple(directory.iterdir())
    except FileNotFoundError:
        return []
    matched: list[tuple[str, str, dict[str, str]]] = []
    for path in paths:
        if not path.is_file() or path.suffix != ".json":
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            if path.name.startswith(f"{session_id}."):
                path.unlink(missing_ok=True)
                matched.append(("", path.name, {"sent_at": "", "body": f"破損した上り通知を削除しました: {path}: {exc}"}))
            continue
        if (
            not isinstance(payload, dict)
            or payload.get("version") != 1
            or payload.get("session_id") != session_id
            or not isinstance(payload.get("sent_at"), str)
            or not isinstance(payload.get("body"), str)
        ):
            if path.name.startswith(f"{session_id}."):
                path.unlink(missing_ok=True)
                matched.append(
                    ("", path.name, {"sent_at": "", "body": f"破損した上り通知を削除しました: {path}: 必須項目が不正です"})
                )
            continue
        notice = {"sent_at": payload["sent_at"], "body": payload["body"]}
        matched.append((payload["sent_at"], path.name, notice))
    matched.sort(key=lambda item: (item[0], item[1]))
    taken: list[dict[str, str]] = []
    for _sent_at, file_name, notice in matched:
        if notice["sent_at"] == "":
            taken.append(notice)
            continue
        try:
            (directory / file_name).unlink()
        except FileNotFoundError:
            continue
        taken.append(notice)
    return taken


def normalize_label(value: str) -> str:
    """依頼本文又はコマンドの最初の空でない行を表示用に正規化する。"""
    line = next((line for line in value.splitlines() if line.strip()), "")
    return " ".join(line.split())[:200]


class StatusFileWriter:
    """共有session辞書をstatusline向け状態ファイルへ集約して書く。"""

    def __init__(
        self,
        sessions: dict[str, SessionState],
        identity: StatusFileIdentity,
        *,
        state_root: pathlib.Path | None = None,
        aggregate_seconds: float = 1.0,
    ) -> None:
        self._sessions = sessions
        self._identity = identity
        self._state_root = state_root
        self._directory = status_directory(identity.root_session_id, state_root)
        self._path = self._directory / identity.file_name
        self._aggregate_seconds = aggregate_seconds
        self._flush_handle: asyncio.TimerHandle | None = None
        self._retention_handle: asyncio.TimerHandle | None = None
        self._published_results: set[str] = set()
        self._projected_host_session_id: str | None = None
        self._active = False

    @property
    def path(self) -> pathlib.Path:
        """自身が所有する状態ファイルのパスを返す。"""
        return self._path

    @property
    def root_session_id(self) -> str:
        """自身が状態ファイルを書き込むルートsession識別子を返す。"""
        return self._identity.root_session_id

    @property
    def sessions(self) -> dict[str, SessionState]:
        """射影元の共有session辞書を返す。"""
        return self._sessions

    def activate(self) -> None:
        """書込を有効化し、前回プロセスの残存状態を初期化する。"""
        try:
            sweep_stale_shared_state(
                keep_root_session_id=self.root_session_id,
                state_root=self._state_root,
            )
        except OSError as exc:
            _LOG.warning("共有状態の期限掃引を開始できませんでした: error=%s", exc)
        self._active = True
        self._remove_owned_and_expired_files()
        self._remove_stale_status_files()
        self.flush()

    def schedule(self) -> None:
        """実行中loopで集約時間後の全置換を予約する。"""
        if not self._active or self._flush_handle is not None:
            return
        loop = asyncio.get_running_loop()
        self._flush_handle = loop.call_later(self._aggregate_seconds, self.flush)

    def flush(self) -> None:
        """表示対象sessionをJSONへ射影して原子的に全置換する。"""
        if not self._active:
            return
        self._flush_handle = None
        self._remove_stale_status_files()
        now = asyncio.get_running_loop().time()
        self._write_terminal_results()
        visible = [
            session
            for session in self._sessions.values()
            if session.announced
            and (session.retention_deadline is None or session.retention_deadline > now)
            and (
                not session.result_available
                or has_uncollected_result(session, self.result_state(session.session_id) == "consumed")
            )
        ]
        visible.sort(key=lambda session: session.started_at)
        payload: dict[str, Any] = {
            "version": 1,
            "host_session_id": self._resolve_host_session_id(),
            "heartbeat_at": datetime.datetime.now(datetime.UTC).isoformat(),
            "updated_at": _updated_at(visible),
            "sessions": [_serialize_session(session) for session in visible],
        }
        atomic_write(self._path, json.dumps(payload, ensure_ascii=False) + "\n")
        self._schedule_retention(visible, now)

    def deactivate(self) -> None:
        """予約を解除し、自身が所有する状態ファイルを削除する。"""
        self._active = False
        for handle in (self._flush_handle, self._retention_handle):
            if handle is not None:
                handle.cancel()
        self._flush_handle = None
        self._retention_handle = None
        self._remove_owned_and_expired_files()
        self._published_results.clear()
        if self._directory.exists() and not any(self._directory.iterdir()):
            self._directory.rmdir()

    def retain_result(self, session: SessionState | SessionResumeState) -> None:
        """保持中又は退避済みsessionの未回収終端結果を残す。"""
        if (
            session.finalized_at is not None
            and session.status in {"completed", "failed", "interrupted"}
            and not session.result_delivered
        ):
            self._write_terminal_result(session)

    def delete_result(self, session_id: str, *, collector: str) -> None:
        """回収済み又は所有解除するsessionの終端結果を削除する。"""
        if not valid_session_id(session_id):
            raise ValueError(f"invalid session_id: {session_id}")
        path = results_directory(self._identity.root_session_id, self._state_root) / f"{session_id}.json"
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        else:
            _LOG.info(
                "result_deleted session_id=%s writer=%s collector=%s",
                session_id,
                self._identity.file_name,
                collector,
            )
        self._published_results.discard(session_id)

    def delete_wait_target(self, session_id: str) -> None:
        """自身の書込主体が登録した待機対象を明示破棄する。"""
        release_wait_target(self.root_session_id, self._identity.file_name, session_id, self._state_root)

    def result_exists(self, session_id: str) -> bool:
        """指定sessionの終端結果ファイルが存在するかを返す。"""
        if not valid_session_id(session_id):
            raise ValueError(f"invalid session_id: {session_id}")
        return (results_directory(self._identity.root_session_id, self._state_root) / f"{session_id}.json").is_file()

    def read_result(self, session_id: str) -> dict[str, Any] | None:
        """指定sessionの保存済み終端結果を検証して返す。"""
        if not valid_session_id(session_id):
            raise ValueError(f"invalid session_id: {session_id}")
        path = results_directory(self._identity.root_session_id, self._state_root) / f"{session_id}.json"
        try:
            payload: Any = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, UnicodeError, json.JSONDecodeError):
            return None
        if (
            not isinstance(payload, dict)
            or payload.get("status") not in TERMINAL_STATUSES
            or not isinstance(payload.get("agent_message"), str)
            or not isinstance(payload.get("turn_seq"), int)
            or isinstance(payload.get("turn_seq"), bool)
            or not isinstance(payload.get("finalized_at"), str)
        ):
            return None
        return payload

    def take_result(self, session_id: str, *, collector: str) -> tuple[dict[str, Any] | None, str | None]:
        """自身が公開した終端結果を一度だけ回収する。"""
        result = take_result(
            self.root_session_id,
            session_id,
            self._identity.file_name,
            collector=collector,
            state_root=self._state_root,
        )
        if result[0] is not None:
            self._published_results.discard(session_id)
        return result

    def result_state(self, session_id: str) -> str:
        """自プロセスが公開した終端結果の公開状態を返す。"""
        if session_id not in self._published_results:
            return "unpublished"
        return "published" if self.result_exists(session_id) else "consumed"

    def take_notices(self, session_id: str) -> list[dict[str, str]]:
        """待機対象sessionの正常な通知を回収する。"""
        return take_notices(self._identity.root_session_id, session_id, self._state_root)

    def _write_terminal_results(self) -> None:
        for session in self._sessions.values():
            if session.result_delivered:
                self.delete_result(session.session_id, collector="status-sync")
                continue
            if not session.result_available:
                continue
            if self.result_state(session.session_id) == "consumed":
                session.result_delivered = True
                self._published_results.discard(session.session_id)
                continue
            self._write_terminal_result(session)

    def _write_terminal_result(self, session: SessionState | SessionResumeState) -> None:
        assert session.finalized_at is not None
        assert session.retention_deadline is not None
        payload = terminal_result_payload(session)
        payload["owner_status_file"] = self._identity.file_name
        directory = results_directory(self._identity.root_session_id, self._state_root)
        atomic_write(directory / f"{session.session_id}.json", json.dumps(payload, ensure_ascii=False) + "\n")
        self._published_results.add(session.session_id)
        _LOG.info("result_written session_id=%s writer=%s", session.session_id, self._identity.file_name)

    def _remove_owned_and_expired_files(self) -> None:
        """自身の状態ファイルと保持期限を超えた通知だけを削除する。"""
        self._path.unlink(missing_ok=True)
        for path in self._directory.glob(f".{self._path.name}.*.tmp"):
            path.unlink()
        results = results_directory(self.root_session_id, self._state_root)
        if results.exists() and not any(results.iterdir()):
            results.rmdir()
        cutoff = datetime.datetime.now(datetime.UTC).timestamp() - RESULT_RETENTION_SECONDS
        directories = (
            notices_directory(self.root_session_id, self._state_root),
            hosts_directory(self.root_session_id, self._state_root),
        )
        for directory in directories:
            if not directory.exists():
                continue
            for path in directory.iterdir():
                if path.is_file() and path.stat().st_mtime < cutoff:
                    path.unlink()
            if not any(directory.iterdir()):
                directory.rmdir()

    def _resolve_host_session_id(self) -> str | None:
        """書込主体に対応する起動元threadを一度だけ状態ファイルへ射影する。"""
        if self._projected_host_session_id is not None:
            return self._projected_host_session_id
        writer_session_id = self._identity.host_session_id
        if writer_session_id is None:
            return None
        path = hosts_directory(self.root_session_id, self._state_root) / f"{writer_session_id}.json"
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, UnicodeError, json.JSONDecodeError):
            return writer_session_id
        host_session_id = payload.get("host_session_id") if isinstance(payload, dict) else None
        if (
            isinstance(payload, dict)
            and payload.get("version") == 1
            and isinstance(host_session_id, str)
            and valid_session_id(host_session_id)
        ):
            self._projected_host_session_id = host_session_id
            return host_session_id
        return writer_session_id

    def _remove_stale_status_files(self) -> None:
        """生存の印が失効した他の書込主体の状態ファイルを削除する。"""
        cutoff = datetime.datetime.now(datetime.UTC) - datetime.timedelta(seconds=HEARTBEAT_EXPIRY_SECONDS)
        for path in self._directory.glob("*.json"):
            if path == self._path:
                continue
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                heartbeat_at = payload.get("heartbeat_at") if isinstance(payload, dict) else None
                heartbeat = datetime.datetime.fromisoformat(heartbeat_at) if isinstance(heartbeat_at, str) else None
            except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
                continue
            if heartbeat is not None and heartbeat.tzinfo is not None and heartbeat < cutoff:
                path.unlink(missing_ok=True)

    def _schedule_retention(self, sessions: list[SessionState], now: float) -> None:
        if self._retention_handle is not None:
            self._retention_handle.cancel()
        deadlines = [
            session.retention_deadline
            for session in sessions
            if session.retention_deadline is not None and session.retention_deadline > now
        ]
        self._retention_handle = None
        if deadlines:
            self._retention_handle = asyncio.get_running_loop().call_at(min(deadlines), self.flush)


def _serialize_session(session: SessionState) -> dict[str, Any]:
    return {
        "session_id": session.session_id,
        "cwd": session.cwd,
        "engine": session.engine,
        "model": session.model,
        "effort": session.effort,
        "model_type": session.model_type,
        "launch_kind": session.launch_kind,
        "prompt": session.prompt,
        "status": session.status,
        "progress": session.progress,
        "last_action": session.last_action,
        "label": session.label,
        "started_at": session.started_at,
        "updated_at": session.updated_at,
        "output_updated_at": session.output_updated_at,
    }


def _updated_at(sessions: list[SessionState]) -> str:
    if sessions:
        return max(session.updated_at for session in sessions)
    return datetime.datetime.now(datetime.UTC).isoformat()

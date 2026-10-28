"""非同期振り返りの待ち集合と、実際に準備した記録範囲を保全する。

活動判定、モデル起動、WI操作は呼出側が所有する。本モジュールは短時間の状態交換だけを直列化する。
"""

from __future__ import annotations

import contextlib
import datetime
import hashlib
import json
import os
import pathlib
import uuid
from collections.abc import Generator, Mapping
from typing import Any

import filelock

from agent_toolkit._common import atomic_file, delegated_session, session_family, session_launchers, state_paths

BATCH_SKILL = "agent-toolkit:session-review-batch"
BATCH_ENV = "AGENT_TOOLKIT_SESSION_REVIEW_BATCH"
TARGET_ENV = "AGENT_TOOLKIT_RUN_SKILL_TARGET_REPO"


def directory() -> pathlib.Path:
    """OSアカウント共通の状態保存先を返す。"""
    return state_paths.state_dir() / "session-review"


def timestamp() -> str:
    """状態へ保存するUTC日時を返す。"""
    return datetime.datetime.now(datetime.UTC).isoformat()


def key(host: str, session_id: str) -> str:
    """hostと会話IDをファイル名へ安全に対応付ける。"""
    if host not in {"claude", "codex", "opencode"} or session_launchers.valid_launcher(session_id) is None:
        raise ValueError("振り返り対象のhostまたは会話IDが不正")
    return f"{host}-{session_id}"


def read(path: pathlib.Path) -> dict[str, Any]:
    """保存済みJSONオブジェクトを読む。破損を空集合へ読み替えない。"""
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSONオブジェクトでない: {path}")
    return value


def write(path: pathlib.Path, value: dict[str, Any]) -> None:
    """状態を原子的に交換する。"""
    atomic_file.atomic_write(path, json.dumps(value, ensure_ascii=False) + "\n")


@contextlib.contextmanager
def locked() -> Generator[None]:
    """記録と完了を同じ短時間ロックで直列化する。"""
    root = directory()
    root.mkdir(parents=True, exist_ok=True)
    with filelock.FileLock(str(root / "state.lock"), timeout=10):
        yield


def pending() -> list[dict[str, Any]]:
    """待ち記録を全件返す。読取不能を不在と扱わない。"""
    return [read(path) for path in sorted((directory() / "pending").glob("*.json"))]


def record(payload: Mapping[str, Any], *, host: str, target_repo: str | None = None) -> bool:
    """最上位の開始または処理済み会話への新しい入力を待ちへ登録する。"""
    session_id = payload.get("session_id")
    if (
        not isinstance(session_id, str)
        or session_launchers.valid_launcher(session_id) is None
        or delegated_session.is_delegated(os.environ)
        or os.environ.get(BATCH_ENV)
        or payload.get("agent_id")
    ):
        return False
    event = payload.get("hook_event_name")
    name = key(host, session_id)
    with locked():
        reviewed = directory() / "reviewed" / f"{name}.json"
        path = directory() / "pending" / f"{name}.json"
        if event != "SessionStart" and not (event == "UserPromptSubmit" and reviewed.exists()):
            return False
        prior = read(path) if path.exists() else {}
        value = {
            **prior,
            "key": name,
            "host": host,
            "session_id": session_id,
            "transcript_path": payload.get("transcript_path") or prior.get("transcript_path"),
            "cwd": payload.get("cwd") or prior.get("cwd"),
            "target_repo": target_repo or prior.get("target_repo"),
            "started_at": prior.get("started_at", timestamp()),
            "updated_at": timestamp(),
            "generation": uuid.uuid4().hex,
        }
        write(path, value)
    return True


def previous_scope(target: Mapping[str, Any]) -> pathlib.Path | None:
    """会話を再開しても保持する前回の処理範囲を返す。"""
    path = directory() / "reviewed" / f"{key(str(target['host']), str(target['session_id']))}.json"
    return path if path.exists() else None


def scope_records(collected: list[Any], *, after: pathlib.Path | None = None) -> dict[str, Any]:
    """解析済みの完全行だけを範囲へ記録し、前回の接頭辞が保たれていることを確かめる。"""
    previous = read(after).get("records", {}) if after is not None else {}
    records: dict[str, Any] = {}
    for item in collected:
        rows = item.records
        prior = previous.get(item.record_id)
        if prior is not None:
            old_rows = [row for row in rows if row.line <= prior["through_line"]]
            digest = hashlib.sha256("\n".join(row.text for row in old_rows).encode()).hexdigest()
            if digest != prior["digest"]:
                raise ValueError(f"前回処理済み範囲を再現できない: {item.record_id}")
        records[item.record_id] = {
            "path": str(item.path),
            "through_line": max((row.line for row in rows), default=0),
            "after_line": prior["through_line"] if prior else 0,
            "digest": hashlib.sha256("\n".join(row.text for row in rows).encode()).hexdigest(),
        }
    missing = set(previous) - set(records)
    if missing:
        raise ValueError(f"前回の子孫記録を再収集できない: {', '.join(sorted(missing))}")
    # 同じ記録の再照会では、summary-onlyを含め保存scopeの内容を一定に保つ。
    return {"version": 1, "records": records}


def complete(prepared: Mapping[str, Any]) -> dict[str, Any]:
    """準備済みの範囲だけを成功確定し、並行する再開と追記を保持する。"""
    if prepared.get("unconfirmed_scope"):
        raise ValueError("未確認範囲を含む準備は成功確定できない")
    scope = read(pathlib.Path(str(prepared["scope_path"])))
    if scope.get("version") != 1 or not isinstance(scope.get("records"), dict):
        raise ValueError("準備済みscopeの形式が不正")
    for entry in scope["records"].values():
        if (
            not isinstance(entry, dict)
            or not isinstance(entry.get("through_line"), int)
            or isinstance(entry["through_line"], bool)
            or entry["through_line"] < 0
        ):
            raise ValueError("準備済みscopeの行境界が不正")
        # prepareと同じJSONL回復・正規化を適用し、元行との一致を判定する。
        loaded = session_family._load_records_checked(entry["path"])  # pylint: disable=protected-access
        if loaded.records is None:
            raise ValueError(f"準備した元記録を再確認できない: {entry['path']}")
        prefix = [row for row in loaded.records if row.line <= entry["through_line"]]
        if hashlib.sha256("\n".join(row.text for row in prefix).encode()).hexdigest() != entry["digest"]:
            raise ValueError(f"準備後に元記録の接頭辞が変化した: {entry['path']}")
    host, session_id = str(prepared["host"]), str(prepared["session_id"])
    name = key(host, session_id)
    with locked():
        path = directory() / "pending" / f"{name}.json"
        target = read(path) if path.exists() else None
        reviewed_path = directory() / "reviewed" / f"{name}.json"
        old = read(reviewed_path) if reviewed_path.exists() else {"records": {}}
        for record_id, entry in scope["records"].items():
            previous = old["records"].get(record_id)
            if previous is not None and previous["through_line"] > entry["through_line"]:
                scope["records"][record_id] = previous
        scope["records"] = {**old["records"], **scope["records"]}
        scope.update(host=host, session_id=session_id)
        write(reviewed_path, scope)
        # 新しいSessionStart/UserPromptSubmitがあれば、同じ会話でも後続世代を消さない。
        appended = any(
            any(line.strip() for line in pathlib.Path(entry["path"]).read_bytes().splitlines()[entry["through_line"] :])
            for entry in scope["records"].values()
        )
        if target is not None and target.get("generation") == prepared.get("generation") and not appended:
            path.unlink()
        elif target is not None and prepared.get("generation") is None:
            # 手動単独分析はprepare開始時に取得した世代を使う。欠落した申告で待ちを削除しない。
            pass
    return {"session_id": session_id, "pending_count": len(pending())}


def prepare_identity(host: str, session_id: str) -> dict[str, Any]:
    """prepare開始時点の待ち世代を取得する。"""
    with locked():
        path = directory() / "pending" / f"{key(host, session_id)}.json"
        return {"host": host, "session_id": session_id, "generation": read(path).get("generation") if path.exists() else None}


def protected_logs() -> set[pathlib.Path]:
    """未確定の振り返りが参照するログを年齢回収から保護する。"""
    protected = set()
    for path in (directory() / "runs").glob("*.json"):
        try:
            value = read(path)
        except OSError, ValueError:
            # 読めないmanifestが参照するログを削除しない。他skillの起動は妨げない。
            return set((state_paths.state_dir() / "run-skill").glob("*session-review-batch*.log"))
        if value.get("status") not in {"completed", "resolved"} and value.get("log_path"):
            protected.add(pathlib.Path(value["log_path"]))
    return protected


def sweep(*, now: float) -> None:
    """完了済み実行だけを30日で回収し、参照を失った処理済み範囲を回収する。"""
    with locked():
        runs = list((directory() / "runs").glob("*.json"))
        waiting = {target["key"] for target in pending()}
        referenced: set[str] = set()
        for path in runs:
            value = read(path)
            if (
                value.get("status") == "failed"
                and value.get("report_synced")
                and all(target["key"] not in waiting for target in value["targets"])
            ):
                value["status"] = "resolved"
                write(path, value)
            if value.get("status") not in {"completed", "resolved"}:
                referenced.update(target["key"] for target in value["targets"])
            elif now - path.stat().st_mtime > 30 * 24 * 60 * 60:
                for prepared in value.get("prepared", []):
                    scope = pathlib.Path(prepared["scope_path"])
                    if scope.parent == path.parent and scope.name.startswith(value["run_id"] + "-"):
                        scope.unlink(missing_ok=True)
                path.unlink()
        referenced.update(waiting)
        launch_log = directory() / "launch.log"
        if not referenced and launch_log.exists() and now - launch_log.stat().st_mtime > 30 * 24 * 60 * 60:
            launch_log.unlink()
        for path in (directory() / "reviewed").glob("*.json"):
            value = read(path)
            if path.stem not in referenced and all(
                not pathlib.Path(item["path"]).exists() for item in value["records"].values()
            ):
                path.unlink()

"""非同期振り返りの待ち集合と、実際に準備した記録範囲を保全する。

活動判定、モデル起動、WI操作は呼出側が所有する。本モジュールは短時間の状態交換だけを直列化する。
"""

from __future__ import annotations

import contextlib
import datetime
import enum
import hashlib
import json
import os
import pathlib
import shlex
import uuid
from collections.abc import Generator, Mapping
from typing import Any, Literal, Self

import filelock
import pydantic

from agent_toolkit._common import (
    atomic_file,
    delegated_session,
    session_family,
    session_launchers,
    session_registry,
    state_paths,
)

BATCH_SCOPE = "agent-toolkit:review-session batch"
LEGACY_BATCH_SCOPE = "agent-toolkit:session-review-batch"
BATCH_ENV = "AGENT_TOOLKIT_SESSION_REVIEW_BATCH"
TARGET_ENV = "AGENT_TOOLKIT_RUN_SKILL_TARGET_REPO"
LAUNCH_ENV = "AGENT_TOOLKIT_SESSION_REVIEW_LAUNCH"


class LaunchStatus(enum.StrEnum):
    """受付前の起動責任と、受付後への移管を区別する。"""

    STARTING = "starting"
    ACCEPTED = "accepted"
    SETTLED = "settled"
    NOTIFIED = "notified"


class LaunchAttempt(pydantic.BaseModel):
    """起動試行の保存境界。OSの生存はProcessIdentityから都度取得する。"""

    model_config = pydantic.ConfigDict(extra="allow", frozen=True)
    status: LaunchStatus
    manifest_path: str | None = None
    exited: pydantic.StrictBool = False
    owner: session_registry.ProcessIdentity | None = None
    process: session_registry.ProcessIdentity | None = None

    @pydantic.model_validator(mode="after")
    def check_receipt(self) -> Self:
        """受付済みの試行は移管先を必須とする。"""
        if (self.status == LaunchStatus.ACCEPTED) != bool(self.manifest_path):
            raise ValueError("起動試行の受付状態とmanifestが一致しない")
        return self

    def save(self, path: pathlib.Path) -> None:
        """検証済みの試行を保存キーの追加なしで交換する。"""
        write(path, self.model_dump(mode="json", exclude_unset=True))

    def receipt(self, manifest: pathlib.Path | None) -> LaunchAttempt:
        """受付済み試行への遅れた見送りで所有移管を取り消さない。"""
        if self.status != LaunchStatus.STARTING:
            return self
        return LaunchAttempt.model_validate(
            {
                **self.model_dump(exclude_unset=True),
                "status": LaunchStatus.ACCEPTED if manifest is not None else LaunchStatus.SETTLED,
                "manifest_path": str(manifest) if manifest else None,
            }
        )

    def attach(self, identity: session_registry.ProcessIdentity | None) -> LaunchAttempt:
        """起動プロセスの発行結果を保存する。"""
        if self.status != LaunchStatus.STARTING:
            raise ValueError("受付終了した試行へプロセスを追加できない")
        return LaunchAttempt.model_validate(
            {
                **self.model_dump(exclude_unset=True),
                "process": identity,
                "exited": identity is None,
            }
        )

    def notify(self) -> LaunchAttempt:
        """受付前に終了した試行だけを通知済みへ移す。"""
        if self.status != LaunchStatus.STARTING:
            raise ValueError("受付終了した試行は失敗通知できない")
        return LaunchAttempt.model_validate({**self.model_dump(exclude_unset=True), "status": LaunchStatus.NOTIFIED})


class BatchStatus(enum.StrEnum):
    """分析・報告の独立した結果をまとめて確定するbatchの段階。"""

    RUNNING = "running"
    FAILED = "failed"
    COMPLETED = "completed"
    RESOLVED = "resolved"


class ReviewBatch(pydantic.BaseModel):
    """既存の保存キーを保持して、状態値と更新条件を検証する。"""

    model_config = pydantic.ConfigDict(extra="allow", frozen=True, strict=True)
    status: BatchStatus
    run_id: str
    targets: list[PendingGeneration]
    prepared: list[dict[str, Any]] = pydantic.Field(default_factory=list)
    analysis_succeeded: bool = False
    report_synced: bool = False
    failure_key: str | None = None
    error: str | None = None

    @pydantic.model_validator(mode="after")
    def check_failure(self) -> Self:
        """失敗状態は診断と報告識別子を必須とする。"""
        if self.status == BatchStatus.FAILED and (not self.failure_key or not self.error):
            raise ValueError("失敗batchにfailure_keyまたはerrorが無い")
        return self


class ScopeEntry(pydantic.BaseModel):
    """元記録の処理済み接頭辞を識別する。"""

    model_config = pydantic.ConfigDict(extra="allow", frozen=True, strict=True)
    path: str
    through_line: int = pydantic.Field(ge=0)
    digest: str


class ReviewedScope(pydantic.BaseModel):
    """pending世代とは独立に残る処理済み範囲。"""

    model_config = pydantic.ConfigDict(extra="allow", frozen=True, strict=True)
    version: Literal[1]
    records: dict[str, ScopeEntry]


class TargetStatus(enum.StrEnum):
    """会話の待ち世代と処理済み範囲の存在を区別する。"""

    ABSENT = "absent"
    PENDING = "pending"
    REVIEWED = "reviewed"
    PENDING_REVIEWED = "pending-reviewed"


class PendingGeneration(pydantic.BaseModel):
    """待ちの入力世代。失敗報告の状態は同じ世代にだけ適用する。"""

    model_config = pydantic.ConfigDict(extra="allow", frozen=True, strict=True)
    generation: str
    key: str
    host: Literal["claude", "codex", "opencode"]
    session_id: str
    failure_key: str | None = None
    failure_unreported: bool = False

    @pydantic.model_validator(mode="after")
    def check_failure(self) -> Self:
        """未報告の失敗には再開対象を識別するキーを伴わせる。"""
        if self.failure_unreported and not self.failure_key:
            raise ValueError("未報告の失敗にfailure_keyが無い")
        if self.key != key(self.host, self.session_id):
            raise ValueError("待ち世代の会話識別子が一致しない")
        return self


class TargetGeneration:
    """pendingとreviewedの世代交換を、呼出側と共通の短時間ロック内で所有する。"""

    def __init__(self, host: str, session_id: str) -> None:
        name = key(host, session_id)
        self.pending_path = directory() / "pending" / f"{name}.json"
        self.reviewed_path = directory() / "reviewed" / f"{name}.json"
        self.pending = PendingGeneration.model_validate(read(self.pending_path)) if self.pending_path.exists() else None
        self.reviewed = ReviewedScope.model_validate(read(self.reviewed_path)) if self.reviewed_path.exists() else None

    @property
    def status(self) -> TargetStatus:
        """保存状態から導出し、別の永続フラグは持たない。"""
        if self.pending is not None:
            return TargetStatus.PENDING_REVIEWED if self.reviewed is not None else TargetStatus.PENDING
        return TargetStatus.REVIEWED if self.reviewed is not None else TargetStatus.ABSENT

    def register(self, value: dict[str, Any]) -> None:
        """新しい入力世代を登録する。"""
        self.pending = PendingGeneration.model_validate(value)
        write(self.pending_path, self.pending.model_dump(mode="json", exclude_unset=True))

    def fail(self, generation: str, failure_key: str, *, reported: bool) -> None:
        """遅れた失敗結果で後続の入力世代を変更しない。"""
        if self.pending is not None and self.pending.generation == generation:
            self.register(
                {**self.pending.model_dump(exclude_unset=True), "failure_key": failure_key, "failure_unreported": not reported}
            )

    def discard(self, generation: str) -> bool:
        """振り返る記録の無い待ち世代を除去する。選定後に再登録された世代は残す。"""
        if self.pending is None or self.pending.generation != generation:
            return False
        self.pending_path.unlink()
        self.pending = None
        return True

    def complete(self, scope: dict[str, Any], generation: str | None, *, appended: bool) -> None:
        """処理範囲を保存し、準備時と一致した待ちだけを除去する。"""
        reviewed = ReviewedScope.model_validate(scope)
        write(self.reviewed_path, scope)
        if self.pending is not None and self.pending.generation == generation and not appended:
            self.pending_path.unlink()
            self.pending = None
        self.reviewed = reviewed


class BatchWriter:
    """別プロセスのfinishと起動側の更新を同じロックで交換する。"""

    def __init__(self, path: pathlib.Path) -> None:
        self.path = path

    def load(self) -> ReviewBatch:
        """保存境界で状態値と各軸の型を検証する。"""
        return ReviewBatch.model_validate_json(self.path.read_text(encoding="utf-8"))

    def create(self, value: dict[str, Any]) -> dict[str, Any]:
        """固定した対象集合の実行を開始する。"""
        with locked():
            if self.path.exists():
                raise ValueError("既存batchは作成し直せない")
            return self._save({**value, "status": BatchStatus.RUNNING})

    def _save(self, value: dict[str, Any]) -> dict[str, Any]:
        batch = ReviewBatch.model_validate_json(json.dumps(value))
        result = batch.model_dump(mode="json", exclude_unset=True)
        write(self.path, result)
        return result

    def _active(self) -> dict[str, Any]:
        batch = self.load()
        if batch.status in {BatchStatus.COMPLETED, BatchStatus.RESOLVED}:
            raise ValueError("確定済みbatchは更新できない")
        return batch.model_dump(exclude_unset=True)

    def fail(self, failure_key: str, reason: str) -> dict[str, Any]:
        """失敗報告を所有する未確定のbatchへ移す。"""
        with locked():
            return self._save({**self._active(), "status": BatchStatus.FAILED, "failure_key": failure_key, "error": reason})

    def report_failure(self, *, synced: bool) -> dict[str, Any]:
        """失敗報告の同期結果を対象の入力世代へ反映する。"""
        with locked():
            value = self._active()
            if value["status"] != BatchStatus.FAILED:
                raise ValueError("失敗していないbatchの失敗報告は確定できない")
            value = self._save({**value, "report_synced": synced})
            for target in value["targets"]:
                TargetGeneration(target["host"], target["session_id"]).fail(
                    target["generation"], value["failure_key"], reported=synced
                )
            return value

    def analyze(self) -> dict[str, Any]:
        """finishは分析成功だけを記録し、範囲の確定は起動側へ残す。"""
        with locked():
            value = self._active()
            if not value.get("prepared") or any(item.get("unconfirmed_scope") for item in value["prepared"]):
                raise ValueError("未準備または未確認範囲のあるbatchは完了できない")
            return self._save({**value, "analysis_succeeded": True})

    def prepare(self, prepared: dict[str, Any]) -> dict[str, Any]:
        """準備済み範囲を同じbatchへ追加する。"""
        with locked():
            value = self._active()
            return self._save({**value, "prepared": [*value.get("prepared", []), prepared]})

    def select(self, targets: list[dict[str, Any]], target_repo: str) -> dict[str, Any]:
        """活動再開や準備失敗を除いた実行対象へ更新する。"""
        with locked():
            value = self._active()
            return self._save({**value, "targets": targets, "target_repo": target_repo})

    def attach(self, identity: session_registry.ProcessIdentity) -> None:
        """生存判定の一次情報を照会できるPID世代を保存する。"""
        with locked():
            self._save({**self._active(), "processes": [{"pid": identity.pid, "create_time": identity.create_time}]})

    def complete(self, *, report_synced: bool) -> dict[str, Any]:
        """分析と報告同期が揃った保存済み範囲だけを成功確定する。"""
        with locked():
            value = self._active()
            if not report_synced:
                raise ValueError("report-unsynced: 報告の同期を確認できない")
            if not value.get("analysis_succeeded"):
                raise ValueError("analysis-incomplete: session-review-state finishの実行が必要")
            for prepared in value["prepared"]:
                complete(prepared)
            return self._save({**value, "status": BatchStatus.COMPLETED})

    def retry_interrupted(self, reason: str) -> dict[str, Any]:
        """診断を残さずに実行プロセスが終了した回を終了扱いにし、対象を待ちに残して次の選定で再試行させる。"""
        with locked():
            value = self._active()
            if value["status"] != BatchStatus.RUNNING:
                raise ValueError("実行中でないbatchは中断の再試行へ移せない")
            return self._save({**value, "status": BatchStatus.RESOLVED, "error": reason, "interrupted_retry": True})

    def resolve(self, *, waiting: set[str] | None = None) -> dict[str, Any]:
        """通常対象が無い回、または失敗対象が解消した回を終了する。"""
        with locked():
            value = self._active()
            if value["status"] == BatchStatus.FAILED:
                if not value.get("report_synced") or waiting is None or any(t["key"] in waiting for t in value["targets"]):
                    raise ValueError("未報告または未解消の失敗batchは解決できない")
            elif any(not target.get("review_batch") for target in value["targets"]):
                raise ValueError("通常対象の残るbatchは解決できない")
            return self._save({**value, "status": BatchStatus.RESOLVED})


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
    with filelock.FileLock(str(root / "state.lock"), timeout=10, is_singleton=True):
        yield


def discard_pending(target: Mapping[str, Any]) -> bool:
    """記録ファイルが作られなかった会話など、振り返る内容の無い待ちを同じ世代に限って除去する。"""
    with locked():
        return TargetGeneration(target["host"], target["session_id"]).discard(target["generation"])


def pending() -> list[dict[str, Any]]:
    """待ち記録を全件返す。読取不能を不在と扱わない。"""
    return [
        PendingGeneration.model_validate(read(path)).model_dump(mode="json", exclude_unset=True)
        for path in sorted((directory() / "pending").glob("*.json"))
    ]


def auto_start_due(*, now: float) -> bool:
    """直前のbatch開始から6時間以上経過したか返す。"""
    for path in (directory() / "runs").glob("*.json"):
        value = read(path)
        started_at = value.get("started_at")
        # 開始日時を保存していない世代は、既存記録の更新日時で保守的に間隔を保つ。
        started = datetime.datetime.fromisoformat(started_at).timestamp() if started_at else path.stat().st_mtime
        if now - started < 6 * 60 * 60:
            return False
    return True


def launch_result(*, manifest: pathlib.Path | None = None) -> None:
    """受付前の試行を正常見送り、または既存の実行記録の責任へ移す。"""
    launch_id = os.environ.get(LAUNCH_ENV)
    if launch_id is None:
        return
    # 環境から任意パスを指定できないよう、生成したUUIDの形式だけを受け付ける。
    if len(launch_id) != 32 or any(character not in "0123456789abcdef" for character in launch_id):
        raise ValueError("振り返りの起動試行IDが不正")
    with locked():
        path = directory() / "launches" / f"{launch_id}.json"
        LaunchAttempt.model_validate(read(path)).receipt(manifest).save(path)


def launch_notice() -> str | None:
    """次の開始時に、受付されず終了した起動試行だけを一度通知する。"""
    failures = []
    with locked():
        for path in sorted((directory() / "launches").glob("*.json")):
            value = read(path)
            attempt = LaunchAttempt.model_validate(value)
            if attempt.status != LaunchStatus.STARTING:
                continue
            if attempt.process is not None and attempt.process.exists():
                continue
            if attempt.process is None and not attempt.exited and (attempt.owner is None or attempt.owner.exists()):
                continue
            # PID保存前に親も終了した試行は、受付記録の無い起動中断である。
            log_path = str(value["log_path"])
            read_command = (
                "Get-Content -LiteralPath '" + log_path.replace("'", "''") + "' -Encoding utf8"
                if os.name == "nt"
                else "cat -- " + shlex.quote(log_path)
            )
            failures.append(f"{value['host']} {value['event']}: `{read_command}`")
            attempt.notify().save(path)
    if not failures:
        return None
    return (
        "振り返りの自動起動が実行受付前に終了した。pendingは保持している。次のコマンドで診断ログを取得せよ: "
        + "、".join(failures)
        + "。原因を確認・是正して`/agent-toolkit:review-session batch`を実行せよ。"
        "この失敗と確認先を、この会話の通常の報告手段でユーザーへ伝えよ。"
    )


def record(payload: Mapping[str, Any], *, host: str, target_repo: str | None = None) -> bool:
    """最上位の開始または処理済み会話への新しい入力を待ちへ登録する。"""
    session_id = payload.get("session_id")
    if (
        not isinstance(session_id, str)
        or session_launchers.valid_launcher(session_id) is None
        or delegated_session.is_delegated(os.environ)
        or payload.get("agent_id")
    ):
        return False
    event = payload.get("hook_event_name")
    name = key(host, session_id)
    with locked():
        state = TargetGeneration(host, session_id)
        if event != "SessionStart" and not (
            event == "UserPromptSubmit" and state.status in {TargetStatus.REVIEWED, TargetStatus.PENDING_REVIEWED}
        ):
            return False
        prior = state.pending.model_dump(exclude_unset=True) if state.pending is not None else {}
        previous = state.reviewed.model_dump(exclude_unset=True) if state.reviewed is not None else {}
        transcript = payload.get("transcript_path") or prior.get("transcript_path")
        # Claudeの準備はtranscript指定で始める。記録先の無いhook再生入力を実対象へ登録しない。
        if host == "claude" and (not isinstance(transcript, str) or not transcript.strip()):
            return False
        value = {
            **prior,
            "key": name,
            "host": host,
            "session_id": session_id,
            "review_batch": bool(os.environ.get(BATCH_ENV) or prior.get("review_batch") or previous.get("review_batch")),
            "transcript_path": transcript,
            "cwd": payload.get("cwd") or prior.get("cwd"),
            "target_repo": target_repo or prior.get("target_repo"),
            "started_at": prior.get("started_at", timestamp()),
            "updated_at": timestamp(),
            "generation": uuid.uuid4().hex,
        }
        state.register(value)
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
    with locked():
        state = TargetGeneration(host, session_id)
        target = state.pending.model_dump(exclude_unset=True) if state.pending is not None else None
        old = state.reviewed.model_dump(exclude_unset=True) if state.reviewed is not None else {"records": {}}
        for record_id, entry in scope["records"].items():
            previous = old["records"].get(record_id)
            if previous is not None and previous["through_line"] > entry["through_line"]:
                scope["records"][record_id] = previous
        scope["records"] = {**old["records"], **scope["records"]}
        scope.update(host=host, session_id=session_id, review_batch=bool((target or old).get("review_batch")))
        # 新しいSessionStart/UserPromptSubmitがあれば、同じ会話でも後続世代を消さない。
        appended = any(
            any(line.strip() for line in pathlib.Path(entry["path"]).read_bytes().splitlines()[entry["through_line"] :])
            for entry in scope["records"].values()
        )
        state.complete(scope, prepared.get("generation"), appended=appended)
    return {"session_id": session_id, "pending_count": len(pending())}


def prepare_identity(host: str, session_id: str) -> dict[str, Any]:
    """prepare開始時点の待ち世代を取得する。"""
    with locked():
        state = TargetGeneration(host, session_id)
        return {
            "host": host,
            "session_id": session_id,
            "generation": state.pending.generation if state.pending is not None else None,
        }


def protected_logs() -> set[pathlib.Path]:
    """未確定の振り返りが参照するログを年齢回収から保護する。"""
    protected = set()
    for path in (directory() / "runs").glob("*.json"):
        try:
            value = read(path)
        except OSError, ValueError:
            # 読めないmanifestが参照するログを削除しない。他skillの起動は妨げない。
            return {
                log
                for pattern in ("*session-review*.log", "*review-session*.log")
                for log in (state_paths.state_dir() / "run-skill").glob(pattern)
            }
        if value.get("status") not in {"completed", "resolved"} and value.get("log_path"):
            protected.add(pathlib.Path(value["log_path"]))
    return protected


def sweep(*, now: float) -> None:
    """完了済み実行だけを30日で回収し、参照を失った処理済み範囲を回収する。"""
    with locked():
        for path in (directory() / "launches").glob("*.json"):
            attempt = LaunchAttempt.model_validate(read(path))
            if attempt.status != LaunchStatus.STARTING and now - path.stat().st_mtime > 30 * 24 * 60 * 60:
                path.with_suffix(".log").unlink(missing_ok=True)
                path.unlink()
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
                value = BatchWriter(path).resolve(waiting=waiting)
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

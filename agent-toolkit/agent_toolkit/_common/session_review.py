"""非同期振り返りの待ち集合と、実際に準備した記録範囲を保全する。

活動判定、モデル起動、WI操作は呼出側が所有する。本モジュールは短時間の状態交換だけを直列化する。
"""

from __future__ import annotations

import contextlib
import datetime
import enum
import hashlib
import json
import logging
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
    mcp_environment,
    session_activity,
    session_family,
    session_launchers,
    session_registry,
    session_state,
    skill_modes,
    state_paths,
)

META_SCOPE = "agent-toolkit:review-session meta"
BATCH_SCOPE = "agent-toolkit:review-session batch"
LEGACY_BATCH_SCOPE = "agent-toolkit:session-review-batch"
BATCH_ENV = "AGENT_TOOLKIT_SESSION_REVIEW_BATCH"
TARGET_ENV = "AGENT_TOOLKIT_RUN_SKILL_TARGET_REPO"
LAUNCH_ENV = "AGENT_TOOLKIT_SESSION_REVIEW_LAUNCH"


class LaunchStatus(enum.StrEnum):
    STARTING = "starting"
    ACCEPTED = "accepted"
    SETTLED = "settled"
    NOTIFIED = "notified"


class LaunchAttempt(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(extra="allow", frozen=True)
    status: LaunchStatus
    manifest_path: str | None = None
    exited: pydantic.StrictBool = False
    exit_code: pydantic.StrictInt | None = None
    owner: session_registry.ProcessIdentity | None = None
    process: session_registry.ProcessIdentity | None = None

    @pydantic.model_validator(mode="after")
    def check_receipt(self) -> Self:
        if (self.status == LaunchStatus.ACCEPTED) != bool(self.manifest_path):
            raise ValueError("起動試行の受付状態とmanifestが一致しない")
        return self

    def save(self, path: pathlib.Path) -> None:
        write(path, self.model_dump(mode="json", exclude_unset=True))

    def receipt(self, manifest: pathlib.Path | None) -> LaunchAttempt:
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
        if self.status != LaunchStatus.STARTING:
            raise ValueError("受付終了した試行は失敗通知できない")
        return LaunchAttempt.model_validate({**self.model_dump(exclude_unset=True), "status": LaunchStatus.NOTIFIED})


class BatchStatus(enum.StrEnum):
    RUNNING = "running"
    USAGE_LIMIT_PAUSED = "usage-limit-paused"
    FAILED = "failed"
    COMPLETED = "completed"
    RESOLVED = "resolved"


class ReviewBatch(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(extra="allow", frozen=True, strict=True)
    status: BatchStatus
    run_id: str
    targets: list[PendingGeneration]
    prepared: list[dict[str, Any]] = pydantic.Field(default_factory=list)
    analysis_succeeded: bool = False
    report_synced: bool = False
    failure_key: str | None = None
    error: str | None = None
    usage_resume_at: float | None = None

    @pydantic.model_validator(mode="after")
    def check_failure(self) -> Self:
        """失敗状態は診断と報告識別子を必須とする。"""
        if self.status == BatchStatus.FAILED and (not self.failure_key or not self.error):
            raise ValueError("失敗batchにfailure_keyまたはerrorが無い")
        if self.status == BatchStatus.USAGE_LIMIT_PAUSED and self.usage_resume_at is None:
            raise ValueError("利用上限で中断したbatchに再開予定時刻が無い")
        return self


class ScopeEntry(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(extra="allow", frozen=True, strict=True)
    path: str
    through_line: int = pydantic.Field(ge=0)
    digest: str


class ReviewedScope(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(extra="allow", frozen=True, strict=True)
    version: Literal[1]
    records: dict[str, ScopeEntry]


class TargetStatus(enum.StrEnum):
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
        if self.pending is not None:
            return TargetStatus.PENDING_REVIEWED if self.reviewed is not None else TargetStatus.PENDING
        return TargetStatus.REVIEWED if self.reviewed is not None else TargetStatus.ABSENT

    def register(self, value: dict[str, Any]) -> None:
        self.pending = PendingGeneration.model_validate(value)
        write(self.pending_path, self.pending.model_dump(mode="json", exclude_unset=True))

    def fail(self, generation: str, failure_key: str, *, reported: bool, version: str | None = None) -> None:
        """遅れた失敗結果で後続の入力世代を変更しない。"""
        if self.pending is not None and self.pending.generation == generation:
            self.register(
                {
                    **self.pending.model_dump(exclude_unset=True),
                    "failure_key": failure_key,
                    "failure_unreported": not reported,
                    "failure_version": version,
                }
            )

    def discard(self, generation: str) -> bool:
        if self.pending is None or self.pending.generation != generation:
            return False
        self.pending_path.unlink()
        self.pending = None
        return True

    def complete(self, scope: dict[str, Any], generation: str | None, *, appended: bool) -> None:
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
        return ReviewBatch.model_validate_json(self.path.read_text(encoding="utf-8"))

    def create(self, value: dict[str, Any]) -> dict[str, Any]:
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

    def fail(
        self, failure_key: str, reason: str, *, version: str | None = None, exit_code: int | None = None
    ) -> dict[str, Any]:
        """失敗報告を所有する未確定のbatchへ移す。"""
        with locked():
            value = self._active()
            return self._save(
                {
                    **value,
                    "status": BatchStatus.FAILED,
                    "failure_key": failure_key,
                    "error": reason,
                    "failure_version": version,
                    "exit_code": exit_code if exit_code is not None else value.get("exit_code"),
                }
            )

    def report_failure(self, *, synced: bool) -> dict[str, Any]:
        """失敗報告の同期結果を対象の入力世代へ反映する。"""
        with locked():
            value = self._active()
            if value["status"] != BatchStatus.FAILED:
                raise ValueError("失敗していないbatchの失敗報告は確定できない")
            value = self._save({**value, "report_synced": synced})
            for target in value["targets"]:
                TargetGeneration(target["host"], target["session_id"]).fail(
                    target["generation"], value["failure_key"], reported=synced, version=value.get("failure_version")
                )
            return value

    def pause_for_usage_limit(self, resume_at: float, exit_code: int) -> None:
        with locked():
            self._save(
                {
                    **self._active(),
                    "status": BatchStatus.USAGE_LIMIT_PAUSED,
                    "usage_resume_at": resume_at,
                    "exit_code": exit_code,
                }
            )

    def resume_usage_limited(self) -> None:
        with locked():
            value = self._active()
            if value["status"] == BatchStatus.USAGE_LIMIT_PAUSED:
                value.pop("usage_resume_at", None)
                self._save({**value, "status": BatchStatus.RUNNING})

    def analyze(self) -> dict[str, Any]:
        with locked():
            value = self._active()
            if not value.get("prepared") or any(
                item.get("unconfirmed_scope") and item.get("partial_analysis") is not True for item in value["prepared"]
            ):
                raise ValueError("未準備または未確認範囲のあるbatchは完了できない")
            return self._save({**value, "analysis_succeeded": True})

    def prepare(self, prepared: dict[str, Any]) -> dict[str, Any]:
        with locked():
            value = self._active()
            return self._save({**value, "prepared": [*value.get("prepared", []), prepared]})

    def select(self, targets: list[dict[str, Any]], target_repo: str) -> dict[str, Any]:
        """活動再開や準備失敗を除いた実行対象へ更新する。"""
        with locked():
            value = self._active()
            return self._save({**value, "targets": targets, "target_repo": target_repo})

    def attach(self, identity: session_registry.ProcessIdentity) -> None:
        with locked():
            self._save(
                {
                    **self._active(),
                    "processes": [identity.payload(legacy=True)],
                    "process_identities": [identity.payload()],
                }
            )

    def complete(self, *, report_synced: bool) -> dict[str, Any]:
        """分析と報告同期が揃った保存済み範囲だけを成功確定する。"""
        with locked():
            value = self._active()
            if not report_synced:
                raise ValueError("report-unsynced: 報告の同期を確認できない")
            if not value.get("analysis_succeeded"):
                raise ValueError("analysis-incomplete: session-review-state finishの実行が必要")
            for prepared in value["prepared"]:
                complete(prepared, allow_partial_analysis=True)
            saved = self._save({**value, "status": BatchStatus.COMPLETED})
            for prepared in saved["prepared"]:
                cleanup_prepared(prepared, allow_partial_analysis=True)
            return saved

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
    return state_paths.state_dir() / "session-review"


def timestamp() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat()


def key(host: str, session_id: str) -> str:
    """hostと会話IDをファイル名へ安全に対応付ける。"""
    if host not in {"claude", "codex", "opencode"} or session_launchers.valid_launcher(session_id) is None:
        raise ValueError("振り返り対象のhostまたは会話IDが不正")
    return f"{host}-{session_id}"


def read(path: pathlib.Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as error:
        raise ValueError(f"{path}: {error}") from error
    if not isinstance(value, dict):
        raise ValueError(f"JSONオブジェクトでない: {path}")
    return value


def write(path: pathlib.Path, value: dict[str, Any]) -> None:
    atomic_file.atomic_write(path, json.dumps(value, ensure_ascii=False) + "\n")


@contextlib.contextmanager
def locked() -> Generator[None]:
    """記録と完了を同じ短時間ロックで直列化する。"""
    root = directory()
    root.mkdir(parents=True, exist_ok=True)
    with filelock.FileLock(str(root / "state.lock"), timeout=10, is_singleton=True):
        yield


def discard_pending(target: Mapping[str, Any]) -> bool:
    with locked():
        return TargetGeneration(target["host"], target["session_id"]).discard(target["generation"])


def failure_version(target: Mapping[str, Any], current: str) -> str:
    """版の無い旧失敗は最初に照会した版で固定し、後続の登録世代は変更しない。"""
    with locked():
        state = TargetGeneration(target["host"], target["session_id"])
        if state.pending is not None and state.pending.generation == target["generation"]:
            value = state.pending.model_dump(exclude_unset=True)
            if not value.get("failure_version"):
                state.register({**value, "failure_version": current})
            return value.get("failure_version") or current
        return target.get("failure_version") or current


def failure_reported(failure_key: str, *, reported: bool | None = None) -> bool:
    name = hashlib.sha256(failure_key.encode()).hexdigest()
    with locked():
        path = directory() / "failures" / f"{name}.json"
        if reported is True:
            write(path, {"failure_key": failure_key, "reported_at": timestamp()})
        elif reported is False:
            path.unlink(missing_ok=True)
        if not path.exists():
            return False
        if read(path).get("failure_key") != failure_key:
            raise ValueError("起票済み失敗の識別子が一致しない")
        return True


def pending() -> list[dict[str, Any]]:
    return [
        PendingGeneration.model_validate(read(path)).model_dump(mode="json", exclude_unset=True)
        for path in sorted((directory() / "pending").glob("*.json"))
    ]


def auto_start_due(*, now: float) -> bool:
    interval = 6 * 60 * 60
    for path in (directory() / "runs").glob("*.json"):
        if now - path.stat().st_mtime >= interval:
            continue
        value = read(path)
        started_at = value.get("started_at")

        started = datetime.datetime.fromisoformat(started_at).timestamp() if started_at else path.stat().st_mtime
        if now - started < interval:
            return False
    return not _consultation_open()


def meta_start_due(*, now: float) -> bool:
    path = directory() / "meta-start.json"
    try:
        started = datetime.datetime.fromisoformat(read(path)["started_at"]).timestamp()
    except FileNotFoundError:
        return not _consultation_open()
    return now - started >= 7 * 24 * 60 * 60 and not _consultation_open()


def _consultation_open() -> bool:
    return any(
        session_state.read_state(session_id).get("add_awi_skill_invoked") is True
        for session_id in session_activity.living_root_session_ids()
    )


def is_review_run() -> bool:
    """非同期振り返り自身は重ねて自動起動せず、通常の分析対象とも区別する。"""
    if os.environ.get(BATCH_ENV):
        return True
    try:
        data = json.loads(os.environ.get(mcp_environment.SESSION_START_INPUT_ENV, ""))
    except ValueError:
        return False
    if not isinstance(data, dict) or not isinstance(skill := data.get("skill"), str):
        return False
    arguments = data.get("arguments")
    return skill_modes.invocation_scope(skill, arguments if isinstance(arguments, str) else None) == META_SCOPE


def _launch_path() -> pathlib.Path | None:
    launch_id = os.environ.get(LAUNCH_ENV)
    if launch_id is None:
        return None
    # 環境から任意パスを指定できないよう、生成したUUIDの形式だけを受け付ける。
    if len(launch_id) != 32 or any(character not in "0123456789abcdef" for character in launch_id):
        raise ValueError("振り返りの起動試行IDが不正")
    return directory() / "launches" / f"{launch_id}.json"


def launch_result(*, manifest: pathlib.Path | None = None) -> None:
    if (path := _launch_path()) is None:
        return
    with locked():
        LaunchAttempt.model_validate(read(path)).receipt(manifest).save(path)


def launch_exited(code: int) -> None:
    if (path := _launch_path()) is None:
        return
    with locked():
        value = read(path)
        attempt = LaunchAttempt.model_validate(value)
        if attempt.status == LaunchStatus.STARTING:
            LaunchAttempt.model_validate({**value, "exited": True, "exit_code": code}).save(path)


def launch_notice(host: str) -> str | None:
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

            log_path = str(value["log_path"])
            read_command = (
                "Get-Content -LiteralPath '" + log_path.replace("'", "''") + "' -Encoding utf8"
                if os.name == "nt"
                else "cat -- " + shlex.quote(log_path)
            )
            code = f" 終了コード{attempt.exit_code}" if attempt.exit_code is not None else ""
            if value.get("mode") == "meta":
                retry = "配布元の作業ツリーで`atk run-skill --args meta review-session`"
            else:
                retry = f"`{skill_modes.entry_command(host, 'review-session batch')}`"
            failures.append(f"{value['host']} {value['event']}{code}: `{read_command}`（原因を是正してから{retry}を実行せよ）")
            attempt.notify().save(path)
    if not failures:
        return None
    return (
        "振り返りの自動起動が実行受付前に終了した。未処理の振り返り対象は保持している。次のコマンドで診断ログを取得せよ: "
        + "、".join(failures)
        + "。"
        "この失敗と確認先を、この会話の通常の報告手段でユーザーへ伝えよ。"
    )


def record(payload: Mapping[str, Any], *, host: str, target_repo: str | None = None) -> bool:
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
            "review_batch": bool(is_review_run() or prior.get("review_batch") or previous.get("review_batch")),
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
    path = directory() / "reviewed" / f"{key(str(target['host']), str(target['session_id']))}.json"
    return path if path.exists() else None


def scope_records(collected: list[Any], *, after: pathlib.Path | None = None) -> dict[str, Any]:
    previous = read(after).get("records", {}) if after is not None else {}
    records: dict[str, Any] = {}
    for item in collected:
        rows = item.records
        prior = previous.get(item.record_id)
        if prior is not None:
            old_rows = [row for row in rows if row.line <= prior.get("observed_through_line", prior["through_line"])]
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

    return {"version": 1, "records": records}


def complete(prepared: Mapping[str, Any], *, allow_partial_analysis: bool = False) -> dict[str, Any]:
    partial = allow_partial_analysis and prepared.get("partial_analysis") is True
    if prepared.get("unconfirmed_scope") and not partial:
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

        boundary = entry.get("observed_through_line", entry["through_line"]) if partial else entry["through_line"]
        if not isinstance(boundary, int) or isinstance(boundary, bool) or boundary < entry["through_line"]:
            raise ValueError("準備済みscopeの観測行境界が不正")
        loaded = session_family._load_records_checked(  # pylint: disable=protected-access
            entry["path"], allow_partial_records=partial
        )
        if loaded.records is None:
            raise ValueError(f"準備した元記録を再確認できない: {entry['path']}")
        prefix = [row for row in loaded.records if row.line <= boundary]
        if hashlib.sha256("\n".join(row.text for row in prefix).encode()).hexdigest() != entry["digest"]:
            raise ValueError(f"準備後に元記録の接頭辞が変化した: {entry['path']}")
    host, session_id = str(prepared["host"]), str(prepared["session_id"])
    with locked():
        state = TargetGeneration(host, session_id)
        target = state.pending.model_dump(exclude_unset=True) if state.pending is not None else None
        old = state.reviewed.model_dump(exclude_unset=True) if state.reviewed is not None else {"records": {}}
        for record_id, entry in scope["records"].items():
            previous = old["records"].get(record_id)
            if previous is not None and previous.get("observed_through_line", previous["through_line"]) > entry.get(
                "observed_through_line", entry["through_line"]
            ):
                scope["records"][record_id] = previous
        scope["records"] = {**old["records"], **scope["records"]}
        if old.get("partial_analysis") or scope.get("partial_analysis"):
            scope["partial_analysis"] = True
            for field in ("unconfirmed_scope", "recovered_record_lines"):
                scope[field] = list(
                    {json.dumps(item, sort_keys=True): item for item in [*old.get(field, []), *scope.get(field, [])]}.values()
                )
        scope.update(host=host, session_id=session_id, review_batch=bool((target or old).get("review_batch")))

        appended = any(
            any(
                line.strip()
                for line in pathlib.Path(entry["path"])
                .read_bytes()
                .split(b"\n")[entry.get("observed_through_line", entry["through_line"]) :]
            )
            for entry in scope["records"].values()
        )
        state.complete(scope, prepared.get("generation"), appended=appended)
    return {"session_id": session_id, "pending_count": len(pending())}


def prepare_identity(host: str, session_id: str) -> dict[str, Any]:
    with locked():
        state = TargetGeneration(host, session_id)
        return {
            "host": host,
            "session_id": session_id,
            "generation": state.pending.generation if state.pending is not None else None,
        }


def cleanup_prepared(
    prepared: Mapping[str, Any], *, completed_before: float | None = None, allow_partial_analysis: bool = False
) -> bool:
    try:
        return _cleanup_prepared(prepared, completed_before=completed_before, allow_partial_analysis=allow_partial_analysis)
    except (OSError, ValueError, KeyError, TypeError) as error:
        logging.getLogger(__name__).warning(
            "bundle回収を保留: %s: %s。対象の内容とアクセス権を確認し、破損した記録は原本から復元してください。"
            "原因の解消後、atk run-skill --args batch agent-toolkit:review-session で回収を再試行できます",
            prepared.get("scope_path", "scopeの所在不明"),
            error,
        )
        return False


def _cleanup_prepared(prepared: Mapping[str, Any], *, completed_before: float | None, allow_partial_analysis: bool) -> bool:
    if prepared.get("unconfirmed_scope") and not (allow_partial_analysis and prepared.get("partial_analysis") is True):
        return False
    scope = pathlib.Path(str(prepared.get("bundle_scope_path", prepared["scope_path"])))
    bundle = scope.parent
    if bundle.name != "bundle" or bundle.is_symlink():
        return False
    for manifest in (directory() / "runs").glob("*.json"):
        try:
            value = read(manifest)
        except (OSError, ValueError) as error:
            logging.getLogger(__name__).warning(
                "bundle回収を保留: %s: %s。対象JSONの内容とアクセス権を確認し、破損した記録は原本から復元してください。"
                "原因の解消後、atk run-skill --args batch agent-toolkit:review-session で回収を再試行できます",
                manifest,
                error,
            )
            return False
        if value.get("status") not in {"completed", "resolved"} and any(
            pathlib.Path(str(item.get("bundle_scope_path", item.get("scope_path", "")))).parent.resolve() == bundle.resolve()
            for item in value.get("prepared", [])
        ):
            return False
    names = {
        "timeline.jsonl",
        "warnings.jsonl",
        "hook-notices.jsonl",
        "conversation.jsonl",
        "resource-usage.jsonl",
        "comparison-materials.jsonl",
    }
    fingerprints = prepared.get("disposable_bundle_files")
    if fingerprints is None:
        try:
            previous = previous_scope(prepared)
            if completed_before is None or previous is None or read(previous)["records"] != read(scope)["records"]:
                logging.getLogger(__name__).info("旧bundleを保持（成功scopeとの一致を未確認、操作不要）: %s", scope)
                return False
        except (OSError, ValueError, KeyError) as error:
            logging.getLogger(__name__).warning(
                "旧bundle回収を保留: %s: %s。scopeと成功済み範囲記録の内容・アクセス権を確認してください。"
                "破損した記録は原本から復元し、atk run-skill --args batch agent-toolkit:review-session で再試行してください",
                scope,
                error,
            )
            return False
        fingerprints = dict.fromkeys(names)
    succeeded = True
    for name, digest in fingerprints.items():
        if name not in names:
            continue
        path = bundle / name
        try:
            if path.is_symlink() or not path.is_file():
                continue
            if digest is None:
                if completed_before is None or path.stat().st_mtime > completed_before:
                    continue
            elif hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                continue
            path.unlink()
        except OSError as error:
            succeeded = False
            logging.getLogger(__name__).warning(
                "bundle回収失敗: %s: %s。対象を使用中の操作が終わるまで待ち、削除権限を確認してください。"
                "原因の解消後、atk run-skill --args batch agent-toolkit:review-session で回収を再試行できます",
                path,
                error,
            )
    return succeeded


def protected_logs() -> set[pathlib.Path]:
    protected = set()
    for path in (directory() / "runs").glob("*.json"):
        try:
            value = read(path)
        except OSError, ValueError:
            return {
                log
                for pattern in ("*session-review*.log", "*review-session*.log")
                for log in (state_paths.state_dir() / "run-skill").glob(pattern)
            }
        if value.get("status") not in {"completed", "resolved"} and value.get("log_path"):
            protected.add(pathlib.Path(value["log_path"]))
    return protected


def sweep(*, now: float) -> None:
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
            cleanup_succeeded = True
            if (
                value.get("status") == "failed"
                and value.get("report_synced")
                and all(target["key"] not in waiting for target in value["targets"])
            ):
                value = BatchWriter(path).resolve(waiting=waiting)
            if value.get("status") not in {"completed", "resolved"}:
                referenced.update(target["key"] for target in value["targets"])
            elif value.get("status") == "completed":
                for prepared in value.get("prepared", []):
                    cleaned = cleanup_prepared(prepared, completed_before=path.stat().st_mtime, allow_partial_analysis=True)
                    cleanup_succeeded = cleaned and cleanup_succeeded
            if (
                cleanup_succeeded
                and value.get("status") in {"completed", "resolved"}
                and now - path.stat().st_mtime > 30 * 24 * 60 * 60
            ):
                for prepared in value.get("prepared", []):
                    scope = pathlib.Path(prepared["scope_path"])
                    if scope.parent == path.parent and scope.name.startswith(value["run_id"] + "-"):
                        scope.unlink(missing_ok=True)
                path.unlink()
        referenced.update(waiting)
        launch_log = directory() / "launch.log"
        if not referenced and launch_log.exists() and now - launch_log.stat().st_mtime > 30 * 24 * 60 * 60:
            launch_log.unlink()
        _sweep_reviewed(referenced)


def sweep_reviewed(*, now: float) -> None:
    with locked():
        referenced = {target["key"] for target in pending()}
        for path in (directory() / "runs").glob("*.json"):
            manifest = read(path)
            try:
                if manifest["status"] != "resolved":
                    referenced.update(target["key"] for target in manifest["targets"])
            except (OSError, ValueError, KeyError, TypeError) as error:
                raise ValueError(f"batchの実行記録を確認できない: {path}: {error}") from error
        _sweep_reviewed(referenced, expired_before=now - 30 * 24 * 60 * 60)


def _sweep_reviewed(referenced: set[str], *, expired_before: float | None = None) -> None:
    for path in (directory() / "reviewed").glob("*.json"):
        if path.stem in referenced:
            continue
        value = read(path)
        try:
            records = value["records"]
            if not isinstance(records, dict):
                raise TypeError("records")
            if (
                expired_before is not None
                and path.stat().st_mtime < expired_before
                or all(not pathlib.Path(item["path"]).exists() for item in records.values())
            ):
                path.unlink()
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise ValueError(f"処理済み範囲を回収できない: {path}: {error}") from error

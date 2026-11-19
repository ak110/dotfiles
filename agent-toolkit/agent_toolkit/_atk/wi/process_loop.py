"""登録全体のprocess-loop。統括会話の実行とリポジトリ別の処理所有を管理する。"""

import argparse
import enum
import json
import os
import pathlib
import sys
import uuid
from typing import Any

import pydantic

from agent_toolkit._atk.wi import listing as _listing
from agent_toolkit._atk.wi import process_loop_completion as _completion
from agent_toolkit._atk.wi import process_loop_env as _env
from agent_toolkit._atk.wi import process_loop_identity as startup_identity
from agent_toolkit._atk.wi import process_loop_launch_gate as _gate
from agent_toolkit._atk.wi import process_loop_log as _log
from agent_toolkit._atk.wi import process_loop_ownership as _ownership
from agent_toolkit._atk.wi import process_loop_scheduled as _scheduled
from agent_toolkit._atk.wi import process_loop_scheduler as _scheduler
from agent_toolkit._atk.wi import process_loop_session as _session
from agent_toolkit._atk.wi import process_loop_worktree as _worktree
from agent_toolkit._atk.wi import submit as _submit
from agent_toolkit._common import console_title as _console_title
from agent_toolkit._common import mcp_environment, process_tree
from agent_toolkit._common import next_action as _next_action
from agent_toolkit._common import session_registry as _registry
from agent_toolkit._common.atomic_file import atomic_write
from agent_toolkit._git import command as _git_command


class RepositoryStatus(enum.StrEnum):
    """対象別結果の公開値を内部の判別にも使う。"""

    OWNER_WAIT = "owner_wait"
    PREPARE_FAILED = "prepare_failed"
    COMPLETED = "completed"
    BLOCKED = "blocked"
    INCOMPLETE = "incomplete"
    PENDING = "pending"
    ABORTED = "aborted"


class RepositoryResult(pydantic.BaseModel):
    """対象別の申告と検収結果。追加の報告内容はそのまま公開JSONへ渡す。"""

    model_config = pydantic.ConfigDict(extra="allow")

    status: RepositoryStatus
    writer_sessions: list[str] = pydantic.Field(default_factory=list)
    independent_submissions: list[_submit.Ticket] = pydantic.Field(default_factory=list)
    reservation_excluded: bool = False

    @pydantic.field_validator("independent_submissions", mode="before")
    @classmethod
    def validate_submissions(cls, value: Any) -> list[_submit.Ticket]:
        """永続入力と所有移管に一致する票だけを製品writerとは別の値として返す。"""
        if not isinstance(value, list):
            raise ValueError("統括結果のindependent_submissionsが不正")
        return [_submit.validate_ticket(item) for item in value]

    @pydantic.field_validator("writer_sessions", mode="before")
    @classmethod
    def validate_writers(cls, value: Any) -> Any:
        """停止確認に使うsessionは空文字を含む入力を拒否する。"""
        if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
            raise ValueError("統括結果のwriter_sessionsが不正")
        return value

    def payload(self) -> dict[str, Any]:
        """未申告の付随項目を増やさず既存のJSON形式へ射影する。"""
        return self.model_dump(mode="json", exclude_unset=True)


def _report(event: str, ownership: _ownership.Ownership, detail: str) -> None:
    _log.append(event, target_repo=ownership.key, session_id=ownership.session_id, detail=detail)
    print(f"{ownership.key}: {detail}", flush=True)


def _finish_repository(ownership: _ownership.Ownership, *, completed: bool) -> None:
    """続行の予定が無くなった担当の所有を解放し、その担当のレーン所有sessionの資源を回収する。

    停止を確認した担当だけを対象とする。回収の可否は`lane.cleanup_session`の
    `_eligible`が判定する保持条件（未統合、使用中、未commitの変更など）が決め、未統合の資源は次の反復の再開（同じレーン所有session）へ残る。
    """
    _ownership.release(ownership, completed=completed)
    _session.cleanup_lane_session(ownership.lane_session_id, target_repo=ownership.repo_id)


def _record_no_progress(snapshot: _listing.RepositorySnapshot, start: _gate.LaunchInput | None) -> None:
    """進捗なしで終わった担当の起動入力を記録し、同じ入力の間は次の反復の判定がその対象の起動を見送る。"""
    repo_id = snapshot.repository.key
    try:
        current = _listing.configured_repo_snapshot(snapshot.private_notes, (snapshot.repository,))[0]
        _gate.record_no_progress(
            repo_id,
            start if start is not None else _gate.repository_input(snapshot, None),
            _gate.repository_input(current, None),
        )
    except (OSError, ValueError) as error:
        # 記録できない場合は次の反復を従来どおり起動させ、他担当の終了処理を続ける。
        next_action = _gate.record_failure_next_action()
        _log.append("no_progress_record_failed", target_repo=repo_id, detail=str(error), next_action=next_action)
        _next_action.report(f"{repo_id}: 無進捗記録を保存できなかった: {error}", next_action=next_action, stream=sys.stdout)


def _read_results(path: pathlib.Path, owners: dict[str, _ownership.Ownership]) -> dict[str, RepositoryResult]:
    """担当が保存した対象別の返却を検証し、欠落を成功として扱わない。

    `owners`と返却のキーは処理対象の識別子（集合JSONの`target`）で、系統の無い対象では`target_repo`と同じになる。

    今回の対象外の記載（明示resumeで再利用した会話に残る前巡回の対象など）は記録して除き、
    今回の対象の検収を続ける。対象外の記載はどの所有の判定にも使われないため、常駐全体を止める理由にしない。
    """
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("repositories"), dict):
        raise ValueError(f"統括結果のrepositoriesが不正: {path}")
    foreign = sorted(data["repositories"].keys() - owners.keys())
    if foreign:
        detail = f"今回の対象外の記載を除いて検収した: {', '.join(foreign)}"
        _log.append("result_foreign_repositories", detail=detail, result_path=str(path))
        _next_action.report(
            f"統括結果に今回の対象外のリポジトリがある: {path}: {detail}",
            next_action="対応不要（今回の対象の結果だけを検収した）。繰り返す場合は統括の結果保存の手順を確認せよ",
            stream=sys.stdout,
        )
    records: dict[str, RepositoryResult] = {}
    for repo_id, record in data["repositories"].items():
        if repo_id not in owners:
            continue
        if not isinstance(record, dict) or record.get("status") not in ("completed", "incomplete", "blocked", "pending"):
            raise ValueError(f"統括結果の対象別状態が不正: {repo_id}")
        result = RepositoryResult.model_validate(record)
        if any(ticket.target_repo != owners[repo_id].repo_id for ticket in result.independent_submissions):
            raise ValueError(f"統括結果のindependent_submissionsの対象が不一致: {repo_id}")
        records[repo_id] = result
    return records


def _writers_stopped(
    owner: _ownership.Ownership,
    *,
    writers: list[str] | None = None,
    other_writers: set[str] | None = None,
) -> bool:
    """レーン登録に結び付く実行主体と、統括の停止で回収できなかった子孫が停止済みの場合だけ回収へ進む。"""
    if _ownership.executor_running(owner) or _ownership.residual_running(owner):
        _ownership.hold(owner, writers=writers)
        return False
    descendants = _ownership.descendant_sessions({owner.session_id} if owner.session_id else set())
    # 他対象が申告した担当とその子孫は、その対象が停止・回収を所有する。
    # 未申告の子孫は所属を確定できないため、各対象の停止条件へ残す。
    descendants.difference_update(_ownership.descendant_sessions(other_writers or set()))
    descendants.update(_ownership.descendant_sessions(set(writers or [])))
    states = {identifier: _registry.resolve(identifier).state for identifier in descendants}
    stopped = all(state not in {_registry.Resolution.RUNNING, _registry.Resolution.UNREADABLE} for state in states.values())
    if not stopped:
        _ownership.hold(owner, writers=sorted(descendants))
        # 読めない登録は担当の終了を確かめられないため回収を保留する。保留を解く回復手段を同じ出力へ示す。
        for identifier, state in sorted(states.items()):
            if state is _registry.Resolution.UNREADABLE:
                path = _registry.registry_directory() / f"{identifier}.json"
                _next_action.report(
                    f"{owner.key}: 担当登録を読めないため回収を保留した: {path.resolve()}",
                    next_action=(
                        f"`atk agents show {identifier}`で担当が終了済みかを確かめ、終了済みと確かめた後に"
                        f"{path.resolve()}を退避または削除する（次の反復で回収を再判定する）"
                    ),
                    stream=sys.stdout,
                )
    return stopped


def _recover_completed(snapshot: _listing.RepositorySnapshot, owner: _ownership.Ownership) -> RepositoryResult | None:
    """親の終了処理が中断した対象について、保存済み申告と実体から完了を確定する。"""
    previous_manifest = (owner.previous or {}).get("manifest")
    if not isinstance(previous_manifest, str):
        return None
    data = json.loads(pathlib.Path(previous_manifest).read_text(encoding="utf-8"))
    result_path = pathlib.Path(data["result_file"])
    returned = _read_results(result_path, {item.get("target", item["target_repo"]): owner for item in data["repositories"]})
    record = returned.get(owner.key)
    if record is None or record.status is not RepositoryStatus.COMPLETED:
        return None
    if any(_completion.remaining_work(snapshot, owner, set())[0].values()):
        return None
    if not _writers_stopped(owner, writers=record.writer_sessions):
        return None
    return record.model_copy(update={"result_path": str(result_path), "session_id": owner.session_id, "recovered": True})


def run_coordinator(
    args: argparse.Namespace,
    snapshots: tuple[_listing.RepositorySnapshot, ...],
    *,
    launch_inputs: dict[str, _gate.LaunchInput] | None = None,
) -> dict[str, dict[str, Any]]:
    """常駐cwdで1つの統括会話を実行し、対象別の成果を検収して巡回を終える。"""
    return {
        repo_id: result.payload() for repo_id, result in _run_coordinator(args, snapshots, launch_inputs=launch_inputs).items()
    }


def _run_coordinator(
    args: argparse.Namespace,
    snapshots: tuple[_listing.RepositorySnapshot, ...],
    *,
    launch_inputs: dict[str, _gate.LaunchInput] | None = None,
) -> dict[str, RepositoryResult]:
    owners: dict[str, _ownership.Ownership] = {}
    targets: dict[str, dict[str, Any]] = {}
    results: dict[str, RepositoryResult] = {}
    by_repo = {snapshot.repository.key: snapshot for snapshot in snapshots}
    cwd = pathlib.Path.cwd().resolve()
    env = _env.child_env()
    requested = getattr(args, "resume", None)
    lookup = getattr(args, "reservation_lookup", False)
    resume = requested
    if resume is None and getattr(args, "auto_resume", False):
        previous = {
            value for snapshot in snapshots if (value := _ownership.coordinator_session(snapshot.repository.key)) is not None
        }
        resume = next(iter(previous)) if len(previous) == 1 else None
    directory = _ownership.ownership_directory() / "rounds"
    directory.mkdir(parents=True, exist_ok=True)
    manifest = directory / f"{uuid.uuid4()}.json"
    result_path = manifest.with_suffix(".result.json")
    try:
        for snapshot in snapshots:
            repository = snapshot.repository
            if lookup:
                continue
            key = repository.key
            try:
                owner = _ownership.try_acquire(
                    repository.repo_id, worktree=repository.worktree, mode="coordinator", branch=repository.branch
                )
            except _ownership.UnreadableOwnershipError as error:
                _next_action.report(error.reason, next_action=error.next_action)
                _log.append("ownership_unreadable", target_repo=key, detail=error.reason, next_action=error.next_action)
                results[key] = RepositoryResult(status=RepositoryStatus.PREPARE_FAILED)
                continue
            if owner is None:
                results[key] = RepositoryResult(status=RepositoryStatus.OWNER_WAIT)
                print(f"{key}: 別のprocess-loopが処理中です。所有の解放を待ちます。", flush=True)
                continue
            owners[key] = owner
            previous_worktree = (owner.previous or {}).get("prepared_worktree")
            resuming = isinstance(previous_worktree, str)
            prepared = _worktree.prepare_session_target(
                pathlib.Path(previous_worktree) if isinstance(previous_worktree, str) else repository.worktree,
                repository.repo_id,
                "",
                worktree_name=args.worktree,
                resume_pending=resuming,
                branch=repository.branch,
            )
            if prepared is None:
                _ownership.release(owner, completed=False)
                owners.pop(key)
                results[key] = RepositoryResult(status=RepositoryStatus.PREPARE_FAILED)
                continue
            target = _session.repository_input(
                snapshot,
                owner,
                worktree=prepared[0],
                reuse_candidates=resume is not None and (owner.previous or {}).get("session_id") == resume,
            )
            if resume is not None and (owner.previous or {}).get("session_id") == resume:
                owner = _ownership.update_session(owner, resume)
                owners[key] = owner
                recovered = _recover_completed(snapshot, owner) if getattr(args, "resume_request_id", None) is None else None
                if recovered is not None:
                    saved_result = manifest.with_name(f"{manifest.stem}.{owner.token}.result.json")
                    recovered = recovered.model_copy(
                        update={"previous_result_path": recovered.payload()["result_path"], "result_path": str(saved_result)}
                    )
                    atomic_write(saved_result, json.dumps(recovered.payload(), ensure_ascii=False) + "\n")
                    results[key] = recovered
                    _gate.clear(key)
                    _finish_repository(owner, completed=True)
                    owners.pop(key)
                    continue
            if "launch_items" not in target:
                start = (launch_inputs or {}).get(key) or _gate.repository_input(snapshot, None)
                target["launch_items"] = sorted(start.items)
                target["launch_alerts"] = sorted(start.alerts) if start.alerts is not None else None
            _ownership.update_prepared(owner, prepared[0], manifest)
            targets[key] = target
            _gate.clear(key)
        if not owners and not lookup:
            return results
        if len(targets) == 1 and not lookup:
            cwd = pathlib.Path(next(iter(targets.values()))["worktree"])
        instruction = ""
        atomic_write(
            manifest,
            json.dumps(
                {
                    "repositories": list(targets.values()),
                    "result_file": str(result_path),
                    "instruction": instruction,
                },
                ensure_ascii=False,
            )
            + "\n",
        )
        result_path.unlink(missing_ok=True)
        prompt = (
            ""
            if requested == ""
            else _session.build_resume_prompt()
            if resume is not None
            else _session.build_process_loop_prompt()
        )
        engine, model, effort, codex_provider, claude_connection = _session.select_available_orchestrator(
            _session.resolve_orchestrator_specs(),
            env,
            cwd,
            resume_session_id=resume,
        )
        env[mcp_environment.SESSION_START_INPUT_ENV] = json.dumps(
            {"source": "process-loop", "skill": "agent-toolkit:process-wi", "mode": "all-repos", "manifest": str(manifest)},
            ensure_ascii=False,
        )
        session_args = argparse.Namespace(**vars(args))
        session_args.resume = resume
        session_args.instruction_manifest = manifest
        if lookup:
            if engine not in {"claude", "codex"} and getattr(args, "interactive_resume", False):
                raise ValueError("このengineにはpickerがないため、再開IDまたはautoを指定する")
            startup_identity.prepare(env)
        selected = False
        resume_only = False
        execution_process: _registry.ProcessIdentity | None = None

        def started(identifier: str) -> None:
            nonlocal selected, resume_only
            if lookup and not selected:
                delivered_instruction = json.loads(manifest.read_text(encoding="utf-8"))["instruction"]
                if requested and requested != identifier:
                    raise ValueError("再開した実会話IDが予約IDと一致しない")
                related = {repo for repo in by_repo if _ownership.coordinator_session(repo) == identifier}
                resume_only = not related
                if not related:
                    info = _registry.resolve(identifier).resume_info
                    if info is not None and info.cwd:
                        common = _git_command.run(
                            ["-C", info.cwd, "rev-parse", "--path-format=absolute", "--git-common-dir"],
                            capture_output=True,
                            text=True,
                            check=False,
                        )
                        if common.returncode == 0:
                            related = {
                                repo
                                for repo, snapshot in by_repo.items()
                                if snapshot.repository.common_dir == pathlib.Path(common.stdout.strip())
                            }
                for repo, snapshot in by_repo.items():
                    if repo not in related:
                        results[repo] = RepositoryResult(status=RepositoryStatus.PENDING, reservation_excluded=True)
                        continue
                    owner = _ownership.try_acquire(
                        snapshot.repository.repo_id,
                        worktree=snapshot.repository.worktree,
                        mode="coordinator",
                        branch=snapshot.repository.branch,
                    )
                    if owner is None:
                        raise ValueError("選択した対象は既に実行中のため再開できない。既存の実行を終了まで待つ")
                    owners[repo] = owner
                    if execution_process is not None:
                        _ownership.update_process(owner, execution_process)
                    if resume_only:
                        owners[repo] = _ownership.record_execution_session(owner, identifier)
                    else:
                        prior = (owner.previous or {}).get("prepared_worktree")
                        prepared = _worktree.prepare_session_target(
                            pathlib.Path(prior) if isinstance(prior, str) else by_repo[repo].repository.worktree,
                            by_repo[repo].repository.repo_id,
                            "",
                            worktree_name=args.worktree,
                            resume_pending=isinstance(prior, str),
                            branch=by_repo[repo].repository.branch,
                        )
                        if prepared is None:
                            raise ValueError("選択した会話の作業ツリーを準備できない")
                        targets[repo] = _session.repository_input(
                            by_repo[repo], owner, worktree=prepared[0], reuse_candidates=True
                        )
                        targets[repo]["launch_items"] = sorted(
                            (launch_inputs or {}).get(repo, _gate.repository_input(by_repo[repo], None)).items
                        )
                        targets[repo]["launch_alerts"] = None
                        _ownership.update_prepared(owner, prepared[0], manifest)
                atomic_write(
                    manifest,
                    json.dumps(
                        {
                            "repositories": list(targets.values()),
                            "result_file": str(result_path),
                            "instruction": delivered_instruction,
                        },
                        ensure_ascii=False,
                    ),
                )
                if output := getattr(args, "worker_output", None):
                    atomic_write(
                        output, json.dumps({repo: result.payload() for repo, result in results.items()}, ensure_ascii=False)
                    )
                selected = True
            for repo_id, owner in tuple(owners.items()):
                if not resume_only:
                    owners[repo_id] = _ownership.update_session(owner, identifier)
            if lookup:
                startup_identity.acknowledge(env, resume_only=resume_only)

        def process_started(process: _registry.ProcessIdentity) -> None:
            nonlocal execution_process
            execution_process = process
            args.resume_started = True
            session_args.resume_request_id = None
            session_args.instruction_manifest = None
            for owner in owners.values():
                _ownership.update_process(owner, process)

        def residual_found(failure: process_tree.ReclaimError) -> None:
            for owner in owners.values():
                _ownership.record_residual(owner, failure.processes)

        execution = _session.run_process_session(
            session_args,
            cwd,
            prompt,
            env,
            orchestrator=engine,
            model=model,
            effort=effort,
            resume_pending=resume is not None,
            on_session_started=started,
            on_process_started=process_started,
            on_residual=residual_found,
            codex_provider=codex_provider,
            claude_connection=claude_connection,
        )
        identifier = env.get(_env.PROCESS_LOOP_SESSION_ID_ENV)
        if identifier:
            started(identifier)
            resume = identifier
        if lookup and (resume_only or not selected):
            for repo, owner in tuple(owners.items()):
                if _writers_stopped(owner):
                    _finish_repository(owner, completed=False)
                else:
                    _ownership.hold(owner)
                results[repo] = RepositoryResult(status=RepositoryStatus.PENDING, reservation_excluded=True)
                owners.pop(repo)
            for repo in by_repo:
                results.setdefault(repo, RepositoryResult(status=RepositoryStatus.PENDING, reservation_excluded=True))
            return results
        returned = _read_results(result_path, owners)
        for repo_id, owner in tuple(owners.items()):
            record = returned.get(repo_id, RepositoryResult(status=RepositoryStatus.INCOMPLETE))
            writers = record.writer_sessions
            other_writers = {
                writer for other, result in returned.items() if other != repo_id for writer in result.writer_sessions
            }
            remaining, excluded = _completion.remaining_work(
                by_repo[repo_id], owner, set(), pathlib.Path(targets[repo_id]["candidate_file"])
            )
            completed = execution and record.status is RepositoryStatus.COMPLETED and not any(remaining.values())
            blocked = record.status is RepositoryStatus.BLOCKED
            saved_result = manifest.with_name(f"{manifest.stem}.{owner.token}.result.json")
            results[repo_id] = record.model_copy(
                update={
                    "status": (
                        RepositoryStatus.COMPLETED
                        if completed
                        else RepositoryStatus.BLOCKED
                        if blocked
                        else RepositoryStatus.INCOMPLETE
                    ),
                    "observed_remaining": remaining,
                    **excluded,
                    "result_path": str(saved_result),
                    "session_id": resume,
                }
            )
            stopped = _writers_stopped(owner, writers=writers, other_writers=other_writers)
            if stopped:
                _finish_repository(owner, completed=bool(completed))
            else:
                _ownership.hold(owner)
                results[repo_id].status = RepositoryStatus.OWNER_WAIT
            if blocked:
                target = targets[repo_id]
                initial = _gate.LaunchInput(
                    frozenset((name, digest, kind) for name, digest, kind in target["launch_items"]),
                    frozenset(target["launch_alerts"]) if target["launch_alerts"] is not None else None,
                )
                _record_no_progress(by_repo[repo_id], initial)
            owners.pop(repo_id)
            targets.pop(repo_id)
            atomic_write(saved_result, json.dumps(results[repo_id].payload(), ensure_ascii=False) + "\n")
        return results
    finally:
        for owner in owners.values():
            if _writers_stopped(owner):
                _finish_repository(owner, completed=False)
            else:
                _ownership.hold(owner)


def cmd_process_loop(args: argparse.Namespace, private_notes: pathlib.Path) -> None:
    """登録全体のprocess-loopを開始し、呼出元の環境を復元する。"""
    previous = {
        _env.PROCESS_LOOP_SESSION_ENV: os.environ.get(_env.PROCESS_LOOP_SESSION_ENV),
        _env.PROCESS_LOOP_SESSION_ID_ENV: os.environ.get(_env.PROCESS_LOOP_SESSION_ID_ENV),
    }
    os.environ[_env.PROCESS_LOOP_SESSION_ENV] = "1"
    os.environ.pop(_env.PROCESS_LOOP_SESSION_ID_ENV, None)
    try:
        with _console_title.console_title("atk wi process-loop"):
            if request := getattr(args, "internal_target", None):
                _scheduler.run_worker(pathlib.Path(request), _run_coordinator)
            else:
                _scheduled.run_loop(args, private_notes)
    finally:
        _env.restore_process_loop_env(previous)

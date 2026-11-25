"""巡回の開始時集合とローカルに取り込まれたWIを比べ、未通知の変化を届ける。  pyfltr: allow-comment"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import time

lazy import filelock

from agent_toolkit._common import process_loop_session, session_state
lazy from agent_toolkit._atk import config
lazy from agent_toolkit._atk.wi import background_sync, listing, sections, snapshot, sync, uwi_scan, wait
lazy from agent_toolkit._atk.wi.constants import WI_TYPE_AWI, WI_TYPE_UWI
lazy from agent_toolkit._atk.wi.frontmatter import parse_frontmatter
lazy from agent_toolkit._common import session_activity, uwi_watch
lazy from agent_toolkit._common.next_action import ActionableError
lazy from agent_toolkit._hooks import uwi_completion

_STATE_KEY = "process_wi_change_notifications"
_PULL_INTERVAL_SECONDS = 60.0


def clear(state: dict) -> None:
    state.pop(_STATE_KEY, None)
    snapshot.clear_direct_start(state)


def _schedule_pull(notes: pathlib.Path, session_id: str, current: dict, now: float) -> None:
    last_fetch = background_sync.read_status(notes).get("last_fetch")
    if isinstance(last_fetch, (int, float)) and now - last_fetch < _PULL_INTERVAL_SECONDS:
        return
    previous = current.get("pull_at")
    if isinstance(previous, (int, float)) and now - previous < _PULL_INTERVAL_SECONDS:
        return
    if not sync.has_remote(notes):
        return

    def reserve(state: dict) -> dict | None:
        observed = state.get(_STATE_KEY, {})
        last = observed.get("pull_at")
        if isinstance(last, (int, float)) and now - last < _PULL_INTERVAL_SECONDS:
            return None
        state[_STATE_KEY] = {**observed, "pull_at": now}
        return state

    if session_state.update_state(session_id, reserve):
        background_sync.schedule(notes)


def _scan(notes: pathlib.Path, sources: dict, notified: set[str]) -> tuple[dict[str, str], dict[str, tuple[str, str]]]:
    configured = {(repo.repo_id, repo.branch): repo for repo in config.resolve_configured_repositories()}
    targets = []
    baselines = {}
    for row in sources.values():
        pair = row["repo"], row["branch"]
        found = configured.get(pair)
        targets.append(found or config.ConfiguredRepository(pair[0], notes, notes, branch=pair[1]))
        baseline = snapshot.read(pathlib.Path(row["path"]), expected_digest=row["digest"])
        baselines[pair] = baseline, {tuple(entry) for entry in row.get("unanswered", ())}
    result: dict[str, str] = {}
    answers: dict[str, tuple[str, str]] = {}
    for scoped in listing.configured_repo_snapshot(notes, tuple(targets)):
        repo = scoped.repository
        baseline, unanswered = baselines[repo.repo_id, repo.branch]
        for path, _target, body, _state, kind in scoped.candidates:
            identity = (repo.repo_id, path.name)
            key = f"{kind}:{path.name}"
            if key in notified or key in result or key in answers:
                continue
            if kind == WI_TYPE_AWI and identity not in baseline:
                label = "処理可能な新着AWI"
            elif (
                kind == WI_TYPE_UWI and uwi_scan.is_uwi_answered(body) and (identity in unanswered or identity not in baseline)
            ):
                label = "新たに回答されたUWI"
            else:
                continue
            title = json.dumps(sections.title(body), ensure_ascii=False)
            line = f"{label} {path.name}（{title}、{repo.repo_id}）"
            submitter = (parse_frontmatter(body) or ({}, ""))[0].get("submitter_session") if kind == WI_TYPE_UWI else None
            if isinstance(submitter, str):
                answers[key] = submitter, line
            else:
                result[key] = line
    return result, answers


def _route_answers(answers: dict[str, tuple[str, str]], result: dict[str, str], session_id: str) -> dict[str, tuple[str, str]]:
    pending: dict[str, tuple[str, str]] = {}
    active_submitters = None
    for key, answer in answers.items():
        submitter, line = answer
        filename = key.removeprefix(f"{WI_TYPE_UWI}:")
        sender = session_state.read_state(submitter)
        if uwi_completion.delivered(sender, filename):
            continue
        if filename in uwi_watch.awaiting(sender):
            if active_submitters is None and submitter != session_id:
                active_submitters = session_activity.living_root_session_ids()
            if submitter == session_id or submitter in (active_submitters or ()):
                pending[key] = answer
                continue
            # 生存判定の間に投入元が受領して終了した場合も、巡回へは重ねて通知しない。  # pyfltr: allow-comment
            if uwi_completion.delivered(session_state.read_state(submitter), filename):
                continue
        result[key] = line
    return pending


def build_notice(session_id: str) -> str | None:
    state = session_state.read_state(session_id)
    if not state.get(session_state.PROCESS_WI_STATE_KEY) and not process_loop_session.is_identified_process_loop_session(
        session_id, os.environ
    ):
        return None
    current = state.get(_STATE_KEY, {})
    try:
        notes = uwi_scan.private_notes_root()
        if notes is None:
            return None
        location = current.get("index_path") if current.get("notes") == str(notes) else None
        index = pathlib.Path(location) if isinstance(location, str) else wait.index_path(notes)
        stamp = wait.index_stamp(index)
        _schedule_pull(notes, session_id, current, time.time())
        if stamp is None or (location is not None and stamp == current.get("stamp") and not current.get("pending_answers")):
            return None
        with sync.repo_lock(notes, timeout=0):
            current = session_state.read_state(session_id).get(_STATE_KEY, {})
            same_index = current.get("notes") == str(notes) and stamp == current.get("stamp")
            if same_index:
                # 投入元の終了・受領はindexを変更しないため、前回保留した回答の宛先だけ判定し直す。  # pyfltr: allow-comment
                result: dict[str, str] = {}
                answers = current.get("pending_answers", {})
            else:
                sources = snapshot.progress_sources(session_id)
                if not sources or any(row.get("notes") != str(notes.resolve()) for row in sources.values()):
                    return None
                sync.ensure_mutation_allowed(notes)
                result, answers = _scan(notes, sources, set(current.get("notified", ())))
            pending = _route_answers(answers, result, session_id)
            if wait.index_stamp(index) != stamp:
                return None
            if same_index and not result and pending == answers:
                return None
            updates = {"notes": str(notes), "index_path": str(index), "stamp": stamp, "pending_answers": pending}
            if not _remember(session_id, updates, result) or not result:
                return None
    except ActionableError, OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError, filelock.Timeout, SystemExit:
        # 走査できなかった版は確定せず、次の操作か既存の30分点検で確認する。  # pyfltr: allow-comment
        return None

    return (
        "WIキューに変化があった: " + "、".join(result.values()) + "。"
        "`agent-toolkit:atk-mode/playbooks/process-wi-immediate-fix.md`「開始後に追加されたWIの確認」に従って着手の時機を判断してください。"
    )


def _remember(session_id: str, updates: dict, result: dict[str, str]) -> bool:
    def remember(value: dict) -> dict:
        previous = value.get(_STATE_KEY, {})
        notified = set(previous.get("notified", ()))
        value[_STATE_KEY] = {
            **previous,
            **updates,
            "notified": sorted(notified | result.keys()),
        }
        return value

    return session_state.update_state(session_id, remember)

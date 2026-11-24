from __future__ import annotations

import contextlib
import dataclasses
import datetime
import json
import os
import pathlib
import threading
import time
from collections.abc import Callable, Generator
from typing import Any

import pytest
from pytilpack import modelcheck

from agent_toolkit._atk.wi import add, entries, frontmatter, operation_permissions, sync, uwi, uwi_scan
from agent_toolkit._common import (
    approval_classifier,
    delegated_session,
    function_hook_requests,
    operation_approvals,
    session_state,
)
from agent_toolkit._git import remote as git_remote
from agent_toolkit._testing import git_repository

_SESSION = "approval-interleavings"
_ORIGIN = "https://github.com/example/repo.git"
_REPO = "github.com/example/repo"
_SESSION_RESOURCE = ("session",)
_UWI_RESOURCE = ("uwi",)
_UWI_LOCK = ("uwi-lock",)
_REQUESTERS = ("A", "B")
_REVOKER = "C"


@dataclasses.dataclass(frozen=True)
class _Scenario:
    key: str
    source: str
    scope: str
    revoke: bool
    failure: str | None = None

    @property
    def limit(self) -> int:
        limit = operation_approvals.operation_limit({"scope": self.scope})
        assert limit is not None
        return limit


_SCENARIOS = tuple(
    _Scenario(
        f"{source}-{scope.replace(':', '')}{'-revoke' if revoke else ''}{f'-{failure}' if failure else ''}",
        source,
        scope,
        revoke,
        failure,
    )
    for source in ("direct", "uwi")
    for scope in ("once", "count:2")
    for revoke in (False, True)
    for failure in ((None, "session-write", "uwi-commit") if source == "uwi" else (None, "session-write"))
)


def _actor() -> str:
    return threading.current_thread().name.removeprefix("modelcheck-")


@dataclasses.dataclass
class _Run:
    events: list[tuple[str, str]] = dataclasses.field(default_factory=list)
    begun: set[str] = dataclasses.field(default_factory=set)
    failed: set[str] = dataclasses.field(default_factory=set)
    committed: set[str] = dataclasses.field(default_factory=set)
    failure_left: bool = False


class _FakeMutation:
    def __init__(self, run: _Run, failure: str | None) -> None:
        self.run = run
        self.failure = failure

    def commit(self, _message: str, _rel_paths: Any) -> None:
        if self.failure == "uwi-commit" and self.run.failure_left and _actor() in _REQUESTERS:
            self.run.failure_left = False
            self.run.failed.add(_actor())
            raise RuntimeError("注入したcommitの失敗")
        if _actor() in _REQUESTERS:
            self.run.committed.add(_actor())


class _FakeStorage:
    def __init__(self, run_ref: Callable[[], _Run], failure: str | None) -> None:
        self.run_ref = run_ref
        self.failure = failure

    @contextlib.contextmanager
    def mutation(self, _root: pathlib.Path, **_kwargs: Any) -> Generator[_FakeMutation]:
        with modelcheck.lock(_UWI_LOCK, label="uwi-mutation"):
            yield _FakeMutation(self.run_ref(), self.failure)


@dataclasses.dataclass(frozen=True)
class _Fixture:
    payload: dict[str, Any]
    request: dict[str, Any]
    state_text: str
    uwi_path: pathlib.Path | None
    uwi_text: str | None


def _prepare(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, scenario: _Scenario) -> _Fixture:
    config = {"allow": [], "environment": [], "soft_deny": ["Local Cleanup: explicit consent required"], "hard_deny": []}
    monkeypatch.setattr(
        approval_classifier, "inspect", lambda value, **_kwargs: approval_classifier.classify(value, config, version="2.1.325")
    )
    monkeypatch.setattr(function_hook_requests, "interactive_available", lambda _session, **_kwargs: True)
    monkeypatch.setattr(git_remote, "origin_url", lambda _cwd, **_kwargs: _ORIGIN)
    monkeypatch.delenv(delegated_session.DELEGATED_SESSION_ENV, raising=False)
    monkeypatch.delenv(delegated_session.OWNER_SESSION_ENV, raising=False)
    work = tmp_path / "work"
    work.mkdir()
    notes = git_repository.init_repository(tmp_path / "notes", commit_message="fixture")
    monkeypatch.setattr(uwi_scan, "private_notes_root", lambda: notes)
    payload = {
        "session_id": _SESSION,
        "tool_name": "Bash",
        "tool_input": {"command": "printf ok"},
        "cwd": str(work),
        "permission_mode": "auto",
        "tool_use_id": "denied",
        "reason": "[Local Cleanup]",
        "hook_event_name": "PermissionDenied",
    }
    operation_permissions.observe_denial(payload)
    request = operation_permissions.pending_requests(_SESSION)[0]["request"]
    if scenario.scope != "once":
        request = operation_permissions.prepare_request(_SESSION, request["request_id"], scope=scenario.scope)
        assert request is not None
    question = "この操作を許可するか。\n\n```json\n" + operation_approvals.request_display(request) + "\n```"
    uwi_path = uwi_text = None
    if scenario.source == "direct":
        options = [{"label": request["allow_choice"]}, {"label": "拒否する"}]
        operation_permissions.observe_answer(
            {
                "session_id": _SESSION,
                "tool_name": "AskUserQuestion",
                "tool_input": {"questions": [{"question": question, "options": options}]},
                "tool_response": {"answers": {question: request["allow_choice"]}},
                "hook_event_name": "PostToolUse",
            }
        )
    else:
        storage = sync.Storage(local_only=True)
        message = frontmatter.serialize_frontmatter({"permission_request": json.dumps(request, ensure_ascii=False)}, question)
        filename = add.add_entries(
            notes,
            messages=[message],
            target_repo=_REPO,
            source="test",
            now=datetime.datetime(2026, 11, 24, tzinfo=datetime.UTC),
            entry_type="uwi",
            question_type="choice",
            choices=request["allow_choice"] + ",拒否する",
            storage=storage,
        )[0]
        assert uwi.answer_uwi(notes, filename=filename, answer=request["allow_choice"], storage=storage)
        uwi_path = entries.entry_path(notes, filename)
        uwi_text = uwi_path.read_text(encoding="utf-8")
    state_text = session_state.state_path(_SESSION).read_text(encoding="utf-8")
    return _Fixture(payload, request, state_text, uwi_path, uwi_text)


def _instrument(monkeypatch: pytest.MonkeyPatch, scenario: _Scenario, run_ref: Callable[[], _Run]) -> None:
    real_read, real_update = session_state.read_state, session_state.update_state
    real_iter, real_write = entries.iter_entries, frontmatter.write_entry_text

    def read_state(session_id: str) -> dict:
        modelcheck.step(_SESSION_RESOURCE, write=False, label="read_state")
        run = run_ref()
        if _actor() not in run.begun:
            run.begun.add(_actor())
            run.events.append(("begin", _actor()))
        return real_read(session_id)

    def update_state(session_id: str, mutator: Callable[[dict], dict | None]) -> bool:
        modelcheck.step(_SESSION_RESOURCE, write=True, label="update_state")
        run = run_ref()
        if scenario.failure == "session-write" and run.failure_left and _actor() in _REQUESTERS:
            run.failure_left = False
            run.failed.add(_actor())
            modelcheck.report_write(False)
            return False
        written = real_update(session_id, mutator)
        modelcheck.report_write(written)
        return written

    def iter_entries(*args: Any, **kwargs: Any) -> Any:
        modelcheck.step(_UWI_RESOURCE, write=False, label="iter_entries")
        return list(real_iter(*args, **kwargs))

    def write_entry_text(path: pathlib.Path, text: str) -> None:
        modelcheck.step(_UWI_RESOURCE, write=True, label="write_entry_text")
        real_write(path, text)

    monkeypatch.setattr(operation_permissions, "read_state", read_state)
    monkeypatch.setattr(operation_permissions, "update_state", update_state)
    monkeypatch.setattr(entries, "iter_entries", iter_entries)
    monkeypatch.setattr(frontmatter, "write_entry_text", write_entry_text)
    monkeypatch.setattr(sync, "DEFAULT_STORAGE", _FakeStorage(run_ref, scenario.failure))


def _approval(fixture: _Fixture, scenario: _Scenario) -> dict[str, Any]:
    if scenario.source == "direct":
        saved = session_state.read_state(_SESSION).get("direct_operation_approvals", {})
        return next(iter(saved.values()))["metadata"].get("operation_approval", {}) if saved else {}
    assert fixture.uwi_path is not None
    parsed = frontmatter.parse_frontmatter(fixture.uwi_path.read_text(encoding="utf-8"))
    return parsed[0].get("operation_approval", {}) if parsed is not None else {}


def _used(approval: dict[str, Any], scenario: _Scenario) -> int | None:
    if "remaining" in approval:
        return scenario.limit - int(approval["remaining"])
    if approval.get("state") == "consumed":
        return scenario.limit
    if approval.get("state") == "approved":
        return 0
    return None


def _recorded(fixture: _Fixture) -> set[str]:
    results = session_state.read_state(_SESSION).get("operation_permission_results", {})
    return {
        name
        for name in _REQUESTERS
        if results.get(f"use-{name}", {}).get("status") == "authorized"
        and results[f"use-{name}"].get("request_id") == fixture.request["request_id"]
    }


def _violations(
    scenario: _Scenario,
    run: _Run,
    allowed: dict[str, bool],
    probe: bool,
    used: int | None,
    recorded: set[str],
) -> list[str]:
    found = []
    granted = sum(allowed.values())
    if granted > scenario.limit:
        found.append("性質1: 承認の回数を超えて許可した")
    if used is not None and granted > used:
        found.append("性質1: 消費した回数を超えて許可した")
    revoked_at = next((index for index, event in enumerate(run.events) if event[0] == "revoked"), None)
    if revoked_at is not None:
        late = [actor for index, (kind, actor) in enumerate(run.events) if kind == "begin" and index > revoked_at]
        if any(allowed.get(actor) for actor in late) or probe:
            found.append("性質2: 取消の完了後に始まった要求を許可した")
    if not scenario.revoke and not run.failed and granted != min(len(_REQUESTERS), scenario.limit):
        found.append("性質3: 取消も保存失敗も無いのに、有効な承認の範囲の要求を拒否した")
    if any(allowed.get(actor) for actor in run.failed):
        found.append("性質4: 保存に失敗した要求を許可した")
    if any(allowed[actor] and actor not in recorded for actor in _REQUESTERS):
        found.append("性質4: 実行記録をセッション状態へ保存していない要求を許可した")
    if scenario.source == "uwi" and any(allowed[actor] and actor not in run.committed for actor in _REQUESTERS):
        found.append("性質4: 消費をUWIへcommitしていない要求を許可した")
    return found


def _check_scenario(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, scenario: _Scenario) -> dict[str, Any]:
    fixture = _prepare(tmp_path, monkeypatch, scenario)
    current = {"run": _Run()}
    _instrument(monkeypatch, scenario, lambda: current["run"])
    state_path = session_state.state_path(_SESSION)

    def setup() -> None:
        current["run"] = _Run(failure_left=scenario.failure is not None)
        state_path.write_text(fixture.state_text, encoding="utf-8")
        if fixture.uwi_path is not None and fixture.uwi_text is not None:
            fixture.uwi_path.write_text(fixture.uwi_text, encoding="utf-8")

    def requester(name: str) -> Callable[[], bool]:
        def run() -> bool:
            payload = {**fixture.payload, "hook_event_name": "PreToolUse", "tool_use_id": f"use-{name}"}
            payload.pop("reason")
            result = operation_permissions.pretooluse(payload)
            return result is not None and result.allow

        return run

    def revoker() -> dict[str, bool]:
        result = operation_permissions.revoke_permission(_SESSION, fixture.request["request_id"])
        if result["complete"]:
            current["run"].events.append(("revoked", _REVOKER))
        return result

    actors: dict[str, Callable[[], Any]] = {name: requester(name) for name in _REQUESTERS}
    if scenario.revoke:
        actors[_REVOKER] = revoker
    explorer = modelcheck.ThreadExplorer(actors, setup, step_timeout=30.0, count_equivalent=False)
    started = time.perf_counter()
    runs = 0
    violations: dict[str, list[str]] = {}
    outcomes: dict[str, int] = {}
    for result in explorer:
        if result.equivalent:
            continue
        runs += 1
        assert not result.exceptions, f"{scenario.key}: 主体が例外で終わった: {result.exceptions!r}、順序: {result.schedule}"
        assert not result.deadlock, f"{scenario.key}: 主体が止まったまま終わった: {result.blocked}"
        run = current["run"]
        allowed = {name: bool(result.returns[name]) for name in _REQUESTERS}
        approval = _approval(fixture, scenario)
        used = _used(approval, scenario)
        recorded = _recorded(fixture)
        probe = False
        if any(event[0] == "revoked" for event in run.events):
            probe_payload = {**fixture.payload, "hook_event_name": "PreToolUse", "tool_use_id": "use-probe"}
            probe_payload.pop("reason")
            decision = operation_permissions.pretooluse(probe_payload)
            probe = decision is not None and decision.allow
        approval = _approval(fixture, scenario)
        outcome = f"許可{sum(allowed.values())}件・保存後{approval.get('state')}/{approval.get('remaining', '-')}"
        outcomes[outcome] = outcomes.get(outcome, 0) + 1
        for violation in _violations(scenario, run, allowed, probe, used, recorded):
            violations.setdefault(violation, [" → ".join(f"{step.actor}:{step.label}" for step in result.steps)])
    assert explorer.outcome is modelcheck.Outcome.EXHAUSTIVE
    return {
        "runs": runs,
        "seconds": time.perf_counter() - started,
        "outcomes": outcomes,
        "violations": violations,
    }


_EXHAUSTIVE = os.environ.get("AGENT_TOOLKIT_OPERATION_APPROVAL_INTERLEAVINGS", "") == "all"


@pytest.mark.parametrize(
    "scenario",
    [
        pytest.param(
            scenario,
            id=scenario.key,
            marks=pytest.mark.skipif(
                scenario.revoke and not _EXHAUSTIVE,
                reason="取消を含む順序の列挙は重いため通常の試験から外す。実行方法は.claude/skills/operation-approval-interleavings/SKILL.md",
            ),
        )
        for scenario in _SCENARIOS
    ],
)
def test_every_interleaving_keeps_approval_contract(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, scenario: _Scenario
) -> None:
    report = _check_scenario(tmp_path, monkeypatch, scenario)
    summary = (
        f"{scenario.key}: 順序{report['runs']}通り、{report['seconds']:.1f}秒、"
        f"結果{dict(sorted(report['outcomes'].items()))}、違反: {sorted(report['violations']) or 'なし'}"
    )
    print(summary)
    for violation, (schedule,) in sorted(report["violations"].items()):
        print(f"  {violation}: {schedule}")
    assert not report["violations"], summary

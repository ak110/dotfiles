from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from agent_toolkit._common import task_documents
from agent_toolkit._common.runtime_inserted import is_runtime_inserted_text
from agent_toolkit._common.session_family import _codex_text_blocks
from agent_toolkit._plan.structure.constants import PLAN_QUEUE_ID_PATTERN
from session_evidence_user_events import _delegation_request

_RESOURCES = re.compile(r'^<atk-auto source="agent-toolkit" kind="plugin-resources">\n.*?\n</atk-auto>\s*', re.S)

_ROLE_HEADER = re.compile(
    r"次の文書の手順を実行せよ（出所: (?:/|[A-Za-z]:[/\\]|\\\\)[^\n]+\.subagent\.md）。\n役割: [^\n]+\n入力:\n"
)
_WRITER_ASSIGNMENT = re.compile(
    r"(?:試行標識: [^\n]+\n)?確定した計画の担当単位を実装せよ。\n計画: (?P<plan>[^\n]+)\n担当WI: (?P<wis>[^\n]+)"
)
_RUN_BRANCH = r"process-wi-[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}-lane-[0-9]{2}"


@dataclass(frozen=True)
class SavedPlan:
    path: Path
    worktree: str
    branch: str | None
    wis: frozenset[str]

    @classmethod
    def from_progress(cls, path: Path, worktree: str, wis: frozenset[str], rows: list[tuple[str, str, str]]) -> SavedPlan:
        pattern = rf"(?:^|[。;]\s*)(?:専用)?worktree={re.escape(worktree)}[、,\s]+branch=({_RUN_BRANCH})(?=[。、,\s]|$)"
        branches = {branch for _, _, note in rows for branch in re.findall(pattern, note.replace("\\", "/"))}
        return cls(path, worktree, branches.pop() if len(branches) == 1 else None, wis)

    def matches(self, reference: PurePosixPath, worktree: str, branch: str | None) -> bool:
        if self.path.name != reference.name or (self.worktree and self.worktree != worktree):
            return False

        key = self.path.parts[-4:]
        if len(key) == 4 and key[0] == "plans" and re.fullmatch(r"\d{4}/\d{2}", "/".join(key[1:3])):
            if reference == PurePosixPath(self.path.as_posix()):
                return True
            if reference.parts[-4:] == key and reference.parts[-5:-4] in {
                ("private-notes",),
                ("$(atk config get private_notes)",),
            }:
                return True
        return reference.parent.parts[-2:] == (".claude", "plans") and self.branch is not None and self.branch == branch


type Assignment = tuple[str | None, frozenset[str]]


def _assignment_fields(text: str) -> Assignment | None:
    task = _delegation_request(_RESOURCES.sub("", text.strip(), count=1))
    if not task:
        return None if is_runtime_inserted_text(text) or not text.strip() else (None, frozenset())
    if _ROLE_HEADER.match(task):
        try:
            fields = task_documents.task_prompt_inputs(task)
        except ValueError:
            return None, frozenset()
        plan, wis = fields.get("計画"), fields.get("担当WI", "")
    elif writer := _WRITER_ASSIGNMENT.match(task):
        plan, wis = writer["plan"], writer["wis"]
    else:
        return (None, frozenset()) if re.search(r"^(?:計画|担当WI):", task, re.M) else None
    assigned_wis = set(re.split(r"[、,\s]+", wis.strip())) if wis else set()
    if any(not PLAN_QUEUE_ID_PATTERN.fullmatch(wi) for wi in assigned_wis):
        return None, frozenset()
    return plan, frozenset(assigned_wis)


@dataclass
class RolloutAssignment:
    assignments: set[Assignment] = field(default_factory=set)
    branches: set[str] = field(default_factory=set)
    calls_started: bool = False
    assigned_before_calls: bool = False

    def observe(self, entry_type: str, payload: dict[str, Any]) -> None:
        if entry_type == "session_meta":
            git = payload.get("git")
            if isinstance(git, dict) and isinstance(branch := git.get("branch"), str):
                self.branches.add(branch)
        elif entry_type == "response_item" and payload.get("role") == "user" and payload.get("type") == "message":
            for text in _codex_text_blocks(payload.get("content")):
                if (assignment := _assignment_fields(text)) is not None:
                    self.assignments.add(assignment)
            if self.assignments and not self.calls_started:
                self.assigned_before_calls = True
        elif payload.get("type") in {"function_call", "custom_tool_call"}:
            self.calls_started = True

    def pair(self, worktree: str, plans: list[SavedPlan]) -> tuple[Path, frozenset[str]] | str:
        if not self.calls_started:
            return "rollouts_without_calls"
        if not self.assigned_before_calls:
            return "rollouts_without_plan_assignment"
        if len(self.assignments) != 1 or len(self.branches) > 1:
            return "rollouts_with_ambiguous_assignment"
        name, wis = next(iter(self.assignments))
        if not name:
            return "rollouts_without_plan_assignment"
        reference = PurePosixPath(name.replace("\\", "/"))
        branch = next(iter(self.branches), None)
        candidates = [plan.path for plan in plans if wis <= plan.wis and plan.matches(reference, worktree, branch)]
        if not candidates:
            return "rollouts_without_matching_plan"
        if len(candidates) > 1:
            return "rollouts_with_ambiguous_assignment"
        return candidates[0], wis

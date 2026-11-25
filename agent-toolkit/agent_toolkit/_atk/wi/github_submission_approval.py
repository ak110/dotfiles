from __future__ import annotations

import datetime
import pathlib
import re

from agent_toolkit._atk.wi import add, alerts, entries, frontmatter
from agent_toolkit._atk.wi.constants import WI_STATE_ADOPTED, WI_STATE_REJECTED, WI_TYPE_AWI
from agent_toolkit._atk.wi.github_submission import ACCEPT_CHOICE, DECLINE_CHOICE, SOURCE, canonical_source
from agent_toolkit._atk.wi.processing_owner import KEY as PROCESSING_OWNER_KEY

_SUBMISSION_PATH = re.compile(r"https://(github\.com/[^/]+/[^/]+)/(issues|pull)/([1-9][0-9]{0,18})")


def resolution(metadata: dict[str, object], answer: str) -> str | None:
    if metadata.get("source") != SOURCE or not isinstance(url := metadata.get("github_source"), str):
        return None
    match = _SUBMISSION_PATH.fullmatch(url)
    repo = metadata.get("target_repo")
    if match is None or not isinstance(repo, str) or match[1] != repo.lower():
        return None
    if canonical_source(repo, match[2], int(match[3])) != url:
        return None
    return {ACCEPT_CHOICE: WI_STATE_ADOPTED, DECLINE_CHOICE: WI_STATE_REJECTED}.get(answer.strip())


def apply(
    private_notes: pathlib.Path,
    path: pathlib.Path,
    metadata: dict[str, object],
    body: str,
    question: str,
    state: str,
) -> list[str]:
    url = metadata["github_source"]
    repo = metadata["target_repo"]
    assert isinstance(url, str) and isinstance(repo, str)
    now = datetime.datetime.now(datetime.UTC)
    changed = [str(path.relative_to(private_notes))]
    if state == WI_STATE_ADOPTED and url not in alerts.existing_github_sources(private_notes, repo, entry_type=WI_TYPE_AWI):
        branch = metadata.get("target_branch")
        for name, _content in add.add_entries_locked(
            private_notes,
            parsed_messages=[({"github_source": url}, question)],
            target_repo=repo,
            source=SOURCE,
            now=now,
            entry_type=WI_TYPE_AWI,
            scope=None,
            question_type=None,
            choices=None,
            item_branches=[branch if isinstance(branch, str) else None],
        ):
            changed.append(str(entries.entry_path(private_notes, name).relative_to(private_notes)))
    metadata["state"] = state
    metadata.pop(PROCESSING_OWNER_KEY, None)
    lines = [f"- 採否: {state}", f"- 処理日時: {now.astimezone().isoformat(timespec='seconds')}"]
    frontmatter.write_entry_text(path, frontmatter.serialize_frontmatter(metadata, entries.append_result_lines(body, lines)))
    return changed

from __future__ import annotations

import argparse
import pathlib
import sys

from agent_toolkit._atk import outcome as _outcome
from agent_toolkit._atk.wi import add as _add
from agent_toolkit._atk.wi import entries as _wi_entries
from agent_toolkit._atk.wi import filenames as _wi_filenames
from agent_toolkit._atk.wi import frontmatter as _frontmatter
from agent_toolkit._atk.wi import priority as _priority
from agent_toolkit._atk.wi import sync as _wi_sync
from agent_toolkit._atk.wi.constants import WI_STATE_INBOX, WI_STATE_PROCESSING, WI_TYPE_AWI
from agent_toolkit._atk.wi.mutations.targets import atomic_write_text, resolve_active_targets
from agent_toolkit._atk.wi.repo import resolve_repo_id
from agent_toolkit._atk.wi.web_input import WebInputError


def set_entry_priority(
    private_notes: pathlib.Path,
    *,
    filename: str,
    urgent: bool,
    reason: dict[str, str] | None = None,
    target_repo: str | None = None,
) -> bool:
    if urgent and (reason is None or any(not reason.get(key, "").strip() for key in _priority.REASON_KEYS)):
        raise WebInputError(
            "urgentには影響・根拠・解除条件がすべて必要です",
            next_action="--impact・--evidence・--releaseを空でない値で指定して再実行する",
        )
    inbox_dir = private_notes / WI_STATE_INBOX
    processing_dir = private_notes / WI_STATE_PROCESSING
    _wi_filenames.validate_filenames_only([filename], inbox_dir)
    with _wi_sync.repo_lock(private_notes):
        _wi_sync.push_pending_commits(private_notes)
        _wi_sync.pull(private_notes)
        path = resolve_active_targets([filename], inbox_dir, processing_dir)[0]
        text = path.read_text(encoding="utf-8")
        parsed = _frontmatter.parse_frontmatter(text)
        if parsed is None:
            raise WebInputError(
                f"frontmatterが破損しているため優先区分を更新できません: {path.name}",
                next_action=f"`atk wi show {path.name}`で保存内容を確認し、ユーザーへ報告する",
            )
        data, body = parsed
        if _wi_entries.entry_type_of(path, text) != WI_TYPE_AWI:
            raise WebInputError(f"AWIだけ優先区分を設定できます: {path.name}", next_action="AWIのファイル名を指定し直す")
        entry_repo = data.get("target_repo")
        if (
            target_repo is not None
            and isinstance(entry_repo, str)
            and resolve_repo_id(entry_repo) != resolve_repo_id(target_repo)
        ):
            raise WebInputError(
                f"target_repoが一致しません: {path.name}",
                next_action=f"`atk wi show {path.name}`で対象リポジトリを確認し、--target-repoを指定し直す",
            )
        for key in (_priority.PRIORITY_KEY, *_priority.REASON_KEYS):
            data.pop(key, None)
        if urgent:
            assert reason is not None
            data[_priority.PRIORITY_KEY] = _priority.URGENT
            for key in _priority.REASON_KEYS:
                data[key] = reason[key].strip()
        updated = _frontmatter.serialize_frontmatter(data, body)
        if updated == text:
            return False
        atomic_write_text(path, updated)
        _wi_sync.commit_and_push(private_notes, "chore: update awi priority", [str(path.relative_to(private_notes))])
        _add.read_saved_entry_details(path, expected_body=updated)
    return True


def cmd_set_priority(args: argparse.Namespace, private_notes: pathlib.Path) -> None:
    target_repo = args.target_repo
    if target_repo is None:
        target_repo, _local_worktree = _add.resolve_add_target(None)
    urgent = args.priority == _priority.URGENT
    reason = (
        {"priority_impact": args.impact or "", "priority_evidence": args.evidence or "", "priority_release": args.release or ""}
        if urgent
        else None
    )
    try:
        changed = set_entry_priority(
            private_notes, filename=args.filename, urgent=urgent, reason=reason, target_repo=target_repo
        )
    except WebInputError as error:
        _outcome.report_failure(f"優先区分の更新を拒否した: {error}", next_action=error.next_action)
        sys.exit(1)
    _outcome.report_success(f"優先区分を{'更新' if changed else '確認'}した: {args.filename}（{args.priority}）")

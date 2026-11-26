import argparse
import contextlib
import dataclasses
import datetime
import json
import os
import pathlib
import re
import subprocess
import sys
import time
from collections.abc import Generator

from agent_toolkit._atk import managed_temp, research_sources
from agent_toolkit._atk import outcome as _outcome
from agent_toolkit._atk.environment import is_agent_environment
from agent_toolkit._atk.wi import add_drafts as _add_drafts
from agent_toolkit._atk.wi import branches as _wi_branches
from agent_toolkit._atk.wi import cli_input as _wi_cli_input
from agent_toolkit._atk.wi import entries as _wi_entries
from agent_toolkit._atk.wi import filenames as _wi_filenames
from agent_toolkit._atk.wi import frontmatter as _frontmatter
from agent_toolkit._atk.wi import sync as _wi_sync
from agent_toolkit._atk.wi import user_comment as _user_comment
from agent_toolkit._atk.wi import uwi_body as _uwi
from agent_toolkit._atk.wi.add_reports import (
    check_run_report_sync,
    compare_draft_openings,
    has_committed_run_report,
    has_synced_run_report,
    print_deferral_candidates,
    print_draft_openings,
)
from agent_toolkit._atk.wi.add_validation import (
    _EMPTY_AWI_ERROR,
    _EMPTY_AWI_NEXT_ACTION,
    _RESERVED_FRONTMATTER_KEYS,
    _parse_leading_frontmatter,
    _require_agent_source,
    _target_repo_error,
    _validate_add_entries,
    body_is_effectively_empty,
    normalize_choices,
    normalize_cooldown_until,
    parse_entry_message,
    require_agent_awi_sections,
    require_cause_analysis_format,
    require_confirmed_cause,
)
from agent_toolkit._atk.wi.constants import DETAILED_KEY, WI_STATE_HOLD, WI_STATE_INBOX, WI_TYPE_AWI, WI_TYPE_UWI
from agent_toolkit._atk.wi.filenames import MISSING_DEPENDENCY_NEXT_ACTION
from agent_toolkit._atk.wi.formatters import shorten_home
from agent_toolkit._atk.wi.repo import resolve_add_target, resolve_head_commit, resolve_repo_id_or_raise
from agent_toolkit._atk.wi.web_input import WebInputError
from agent_toolkit._common import body_match as _body_match
from agent_toolkit._common import delegated_session, external_source_review, session_review, skill_modes, weekly_work
from agent_toolkit._common import process_loop_session as _process_loop_session
from agent_toolkit._common import uwi_watch as _uwi_watch
from agent_toolkit._common.session_state import read_state
from agent_toolkit._git import command as _git_command
from agent_toolkit._plan import owner_records as _owner_records

_DRAFT_NEXT = "上流があれば取得し、無ければ宛先ブランチを使って基点を選び直す。影響する前提を調べ、原稿を検収し直す"


@dataclasses.dataclass(frozen=True)
class DraftBasis:
    repo: str
    worktree: pathlib.Path
    branch_ref: str
    oid: str

    def verify(self, registered_branch: str | None) -> None:
        def git(*args: str) -> subprocess.CompletedProcess[str]:
            return _git_command.run(["-C", str(self.worktree), *args], capture_output=True, text=True, check=False, timeout=10)

        if not self.branch_ref.startswith("refs/heads/") or git("check-ref-format", self.branch_ref).returncode != 0:
            raise WebInputError("起草のベースにはrefs/heads/から始まる完全なrefが必要", next_action=_DRAFT_NEXT)
        if re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", self.oid) is None:
            raise WebInputError("起草の基点には完全なOIDが必要", next_action=_DRAFT_NEXT)
        branch = self.branch_ref.removeprefix("refs/heads/")
        if registered_branch is not None:
            if registered_branch != branch:
                raise WebInputError("起草のベースが登録済みの宛先系統と一致しない", next_action=_DRAFT_NEXT)
            ref = f"refs/remotes/origin/{branch}"
        else:
            current_branch = git("symbolic-ref", "--quiet", "HEAD")
            if current_branch.returncode != 0 or current_branch.stdout.strip() != self.branch_ref:
                raise WebInputError("起草のベースが宛先worktreeの現在のブランチと一致しない", next_action=_DRAFT_NEXT)
            result = git("for-each-ref", "--format=%(refname)%09%(upstream)", self.branch_ref)
            if result.returncode != 0:
                raise WebInputError("起草のベースrefを照会できない", next_action=_DRAFT_NEXT)
            selected = next(
                (line.partition("\t")[2] for line in result.stdout.splitlines() if line.partition("\t")[0] == self.branch_ref),
                None,
            )
            if selected is None:
                raise WebInputError("起草のベースrefが宛先のリポジトリに無い", next_action=_DRAFT_NEXT)
            if not selected:
                configured = (git("config", "--get", f"branch.{branch}.{key}") for key in ("remote", "merge"))
                if any(setting.returncode != 1 for setting in configured):
                    raise WebInputError("設定済みの宛先上流を解決できない", next_action=_DRAFT_NEXT)
            ref = selected or self.branch_ref
        current = git("rev-parse", "--verify", f"{ref}^{{commit}}")
        if current.returncode != 0:
            raise WebInputError(f"取得済みの宛先refを解決できない: {ref}", next_action=_DRAFT_NEXT)
        if current.stdout.strip() != self.oid:
            raise WebInputError(f"起草の基点が取得済みの宛先refと一致しない: {ref}", next_action=_DRAFT_NEXT)


def _ensure_draft_destinations(
    basis: DraftBasis,
    messages: list[tuple[dict[str, object], str]],
    branches: list[str | None],
    commit_branch: str | None,
) -> None:
    for (metadata, _), branch in zip(messages, branches, strict=True):
        raw = metadata.get("target_repo", basis.repo)
        if not isinstance(raw, str) or resolve_repo_id_or_raise(raw) != basis.repo or branch != commit_branch:
            raise WebInputError("起草の基点を使う原稿の宛先が別repoまたは別系統", next_action=_DRAFT_NEXT)


_WEEKLY_SLOT = weekly_work.SLOT_PATTERN


def _active_batch() -> tuple[session_review.BatchWriter, session_review.ReviewBatch] | None:
    raw = os.environ.get(session_review.BATCH_ENV)
    if not raw:
        return None
    try:
        path = pathlib.Path(raw).resolve()
        if path.parent != (session_review.directory() / "runs").resolve():
            return None
        writer = session_review.BatchWriter(path)
        batch = writer.load()
    except OSError, ValueError:
        return None
    if batch.run_id != path.stem or batch.status not in {session_review.BatchStatus.RUNNING, session_review.BatchStatus.FAILED}:
        return None
    return writer, batch


def _active_report_batch(scope: object) -> tuple[session_review.BatchWriter, session_review.ReviewBatch] | None:
    if isinstance(scope, str) and skill_modes.canonical_scope(scope) == session_review.BATCH_SCOPE:
        return _active_batch()
    return None


@contextlib.contextmanager
def _track_batch_report_save(
    private_notes: pathlib.Path,
    entry_type: str,
    source: str | None,
    scope: str | None,
    parsed_messages: list[tuple[dict[str, object], str]],
) -> Generator[None]:
    active = _active_report_batch(scope) if entry_type == WI_TYPE_UWI else None
    if active is None or not any(
        data.get("source", source) == "run-skill" and data.get("run_skill_report") == "true" for data, _ in parsed_messages
    ):
        yield
        return
    writer, batch = active
    details = {"notes": str(private_notes)}
    writer.record_report_step("save-start", {**details, "status": "attempted"})
    status = "failed"
    try:
        expected_notes = session_review.resolve_report_notes(batch.report_notes)
        if private_notes.resolve() != expected_notes.resolve():
            status = "wrong-destination"
            raise WebInputError(
                "振り返りの報告は起動元と同じprivate-notesへ保存する",
                next_action="保存先の上書きを外し、振り返りbatch内で保存し直す",
            )
        yield
        status = "returned"
    finally:
        writer.record_report_step("save-result", {**details, "status": status})


def run_report_metadata(
    data: dict[str, object], *, question_type: str | None = None, scope: str | None = None, new_report: bool = False
) -> dict[str, object]:
    raw_scope = scope if scope is not None else data.get("scope")
    if (
        new_report
        and data.get("source") == "run-skill"
        and data.get("run_skill_report") == "true"
        and (active := _active_report_batch(raw_scope))
    ):
        return {**data, "run_skill_run_id": active[1].run_id}
    if data.get("run_skill_run_id"):
        return data
    run_id = os.environ.get("AGENT_TOOLKIT_RUN_SKILL_ID")
    if not run_id:
        return data
    if data.get("run_skill_report") == "true":
        data = {**data, "run_skill_run_id": run_id}
    slot = os.environ.get(session_review.WEEKLY_SLOT_ENV, "")
    kind = question_type if question_type is not None else data.get("question_type")
    if (
        (match := _WEEKLY_SLOT.fullmatch(slot)) is not None
        and data.get("source") == "run-skill"
        and isinstance(raw_scope, str)
        and skill_modes.canonical_scope(raw_scope) == weekly_work.DEFINITIONS[match["work"]].scope
        and (data.get("run_skill_report") == "true" or kind == "choice")
    ):
        data = {**data, "run_skill_run_id": run_id, "weekly_slot": slot}
    return data


def _saved_mismatch_next_action(filename: str) -> str:
    return f"保存は済んでいる。`atk wi show {filename}`で確認し、`atk wi edit {filename} --body-file <本文ファイル>`で直す"


def read_saved_entry_details(path: pathlib.Path, *, expected_body: str) -> dict[str, object | None]:
    saved_body = _frontmatter.decode_entry_text(path.read_bytes())
    parsed = _frontmatter.parse_frontmatter(saved_body)
    if parsed is None:
        raise WebInputError(
            f"保存済みエントリのfrontmatterを読み込めません: {path.name}", next_action=_saved_mismatch_next_action(path.name)
        )
    data, _body = parsed
    raw_dependencies = data.get("depends_on")
    depends_on = [value for value in raw_dependencies if isinstance(value, str)] if isinstance(raw_dependencies, list) else []
    if _body_match.verdict(expected_body, saved_body) != "一致":
        position = _body_match.first_difference(expected_body, saved_body)
        raise WebInputError(
            f"保存本文が送信元本文と一致しない: {path.name}\n"
            f"最初の差異: {position}文字目\n"
            f"送信元本文:\n{expected_body}\n"
            f"保存本文:\n{saved_body}",
            next_action=_saved_mismatch_next_action(path.name),
        )
    return {
        "target_repo": data.get("target_repo"),
        **({_wi_branches.KEY: data[_wi_branches.KEY]} if _wi_branches.KEY in data else {}),
        "target_commit": data.get("target_commit"),
        "depends_on": depends_on,
        "cooldown_until": data.get("cooldown_until"),
        "source": data.get("source"),
        "extra_frontmatter": {key: value for key, value in data.items() if key not in _RESERVED_FRONTMATTER_KEYS},
    }


def print_entry_details(details: dict[str, object | None]) -> None:
    for key in ("target_repo", "target_branch", "target_commit"):
        value = details.get(key)
        if key == "target_branch" and value is None:
            continue
        print(f"    {key}: {value if value is not None else 'なし'}")
    depends_on = details["depends_on"]
    rendered_dependencies = (
        "、".join(str(value) for value in depends_on) if isinstance(depends_on, (list, tuple)) and depends_on else "なし"
    )
    print(f"    depends_on: {rendered_dependencies}")
    cooldown_until = details.get("cooldown_until")
    print(f"    cooldown_until: {cooldown_until if cooldown_until is not None else 'なし'}")
    source = details["source"]
    print(f"    source: {source if source is not None else 'なし'}")
    extra_frontmatter = details["extra_frontmatter"]
    rendered_extra_frontmatter = (
        json.dumps(extra_frontmatter, ensure_ascii=False, default=str)
        if isinstance(extra_frontmatter, dict) and extra_frontmatter
        else "なし"
    )
    print(f"    extra_frontmatter: {rendered_extra_frontmatter}")


def _normalize_dependencies(values: list[str] | None, inbox_dir: pathlib.Path) -> tuple[str, ...]:
    return tuple(dict.fromkeys(_wi_filenames.validate_filename(value, inbox_dir).name for value in (values or ())))


def _missing_dependency_warnings(
    private_notes: pathlib.Path,
    inbox_dir: pathlib.Path,
    generated: list[str],
    dependencies: tuple[str, ...],
) -> list[str]:
    if not dependencies:
        return []
    return _wi_filenames.missing_dependency_warnings(
        [(filename, dependency) for filename in generated for dependency in dependencies],
        resolvable=_wi_filenames.existing_entry_filenames(private_notes),
        case_sensitive=_wi_filenames.is_case_sensitive(inbox_dir),
    )


def _resolve_submitter_session() -> str | None:
    """UWIを投入したセッションの識別子を環境から解決する。

    委譲先は委譲元のセッションを返すため、委譲先が投入したUWIも委譲元のセッションへ通知される。
    解決できない場合と、会話へ対応しないプロセス専用の識別子だった場合は`None`を返す。
    計画ファイルを所有する会話の判定と同じ処理を使う。
    """
    return _owner_records.resolve_conversation_session_id()


def add_entries_locked(
    private_notes: pathlib.Path,
    *,
    parsed_messages: list[tuple[dict[str, object], str]],
    target_repo: str | None,
    source: str | None,
    now: datetime.datetime,
    entry_type: str,
    scope: str | None,
    question_type: str | None,
    choices: str | None,
    target_commit: str | None = None,
    repair_targets: list[str | None] | None = None,
    repair_kinds: list[str | None] | None = None,
    depends_on: tuple[str, ...] = (),
    cooldown_until: str | None = None,
    submitter_session: str | None = None,
    item_branches: list[str | None] | None = None,
    target_commit_branch: str | None = None,
    initial_state: str = WI_STATE_INBOX,
    insertions: list[_add_drafts.Insertion] | None = None,
    detailed: bool = False,
) -> list[tuple[str, str]]:
    """取得済みrepoロック内でエントリを書き込み、生成ファイル名と確定本文の組を返す。

    `target_repo`が`None`の場合は各メッセージのfrontmatterの`target_repo`だけを採用する
    （呼び出し元の`add_entries`が全件の存在と解決可否を検証済みとする）。
    `item_branches`は呼び出し元が検証済みの各メッセージの宛先の系統で、`target_commit`はその系統が
    `target_commit_branch`と一致する項目にだけ記録する。
    """
    effective_branches: list[str | None] = [None for _ in parsed_messages] if item_branches is None else item_branches
    effective_repair_targets: list[str | None] = [None for _ in parsed_messages] if repair_targets is None else repair_targets
    effective_repair_kinds: list[str | None] = [None for _ in parsed_messages] if repair_kinds is None else repair_kinds
    if len(effective_repair_targets) != len(parsed_messages):
        raise ValueError("repair_targetsの件数がメッセージ件数と一致しません")
    if len(effective_repair_kinds) != len(parsed_messages):
        raise ValueError("repair_kindsの件数がメッセージ件数と一致しません")
    if any(
        (repair_target is None) != (repair_kind is None)
        for repair_target, repair_kind in zip(effective_repair_targets, effective_repair_kinds, strict=True)
    ):
        raise ValueError("repair_targetとrepair_kindは同時に指定してください")
    timestamp = now.strftime("%Y%m%d-%H%M%S")
    counter = _wi_filenames.max_existing_seq(private_notes, timestamp) + 1 if insertions is None else 0
    generated: list[tuple[str, str]] = []
    for index, ((frontmatter, body), repair_target, repair_kind, item_branch) in enumerate(
        zip(
            parsed_messages,
            effective_repair_targets,
            effective_repair_kinds,
            effective_branches,
            strict=True,
        )
    ):
        raw_target_repo = frontmatter.get("target_repo", target_repo)
        raw_source = frontmatter.get("source", source)
        try:
            item_target_repo = (
                resolve_repo_id_or_raise(raw_target_repo, warn_non_origin=True)
                if isinstance(raw_target_repo, str)
                else target_repo
            )
        except WebInputError as error:
            raise _target_repo_error(raw_target_repo, error) from error
        item_source = raw_source if isinstance(raw_source, str) else source
        if insertions is not None:
            filename = insertions[index].filename
            item_dependencies = insertions[index].depends_on
        else:
            filename = f"{timestamp}-{counter:03d}.md"
            item_dependencies = depends_on
        frontmatter_data: dict[str, object] = {"target_repo": item_target_repo}
        if item_branch is not None:
            frontmatter_data[_wi_branches.KEY] = item_branch
        frontmatter_data.update(type=entry_type, state=initial_state)
        if target_commit is not None and item_target_repo == target_repo and item_branch == target_commit_branch:
            frontmatter_data["target_commit"] = target_commit
        if item_source:
            frontmatter_data["source"] = item_source
        frontmatter_data.update((key, value) for key, value in frontmatter.items() if key not in _RESERVED_FRONTMATTER_KEYS)
        for key in (_add_drafts.FINGERPRINT, "github_source"):
            if key in frontmatter:
                frontmatter_data[key] = frontmatter[key]
        if entry_type == WI_TYPE_UWI:
            frontmatter_data = run_report_metadata(frontmatter_data, question_type=question_type, scope=scope, new_report=True)
        if entry_type != WI_TYPE_AWI:
            if scope:
                frontmatter_data["scope"] = scope
            frontmatter_data["question_type"] = question_type
            if choices:
                frontmatter_data["choices"] = choices
            if repair_target is not None:
                frontmatter_data["repair_target"] = repair_target
                frontmatter_data["repair_kind"] = repair_kind
            if submitter_session is not None:
                frontmatter_data["submitter_session"] = submitter_session
            logical_body = f"\n{_uwi.QUESTION_HEADING}\n\n{body}\n\n{_uwi.ANSWER_HEADING}\n\n{_uwi.ANSWER_MARKER}\n"
        else:
            logical_body = body if body.startswith("\n") else f"\n{body.rstrip()}\n"
            if item_dependencies:
                frontmatter_data["depends_on"] = list(item_dependencies)
            if cooldown_until is not None:
                frontmatter_data["cooldown_until"] = cooldown_until
            if detailed:
                frontmatter_data[DETAILED_KEY] = True
        content = _frontmatter.normalize_newlines(_frontmatter.serialize_frontmatter(frontmatter_data, logical_body))
        path = _wi_entries.entry_path(private_notes, filename)
        path.parent.mkdir(parents=True, exist_ok=True)
        _frontmatter.write_entry_text(path, content)
        if entry_type != WI_TYPE_AWI:
            _uwi.warn_question_quality(filename, body, question_type)
        generated.append((filename, content))
        counter += 1
    return generated


def add_entries(
    private_notes: pathlib.Path,
    *,
    messages: list[str],
    target_repo: str | None,
    source: str | None,
    now: datetime.datetime,
    entry_type: str = WI_TYPE_AWI,
    scope: str | None = None,
    question_type: str | None = None,
    choices: str | None = None,
    target_commit: str | None = None,
    depends_on: tuple[str, ...] = (),
    cooldown_until: str | None = None,
    storage: _wi_sync.Storage = _wi_sync.DEFAULT_STORAGE,
    saved_details: dict[str, dict[str, object | None]] | None = None,
    submitter_session: str | None = None,
    target_branch: str | None = None,
    initial_state: str = WI_STATE_INBOX,
    draft_basis: DraftBasis | None = None,
    draft_namespace: str | None = None,
    resolve_draft_links: bool = True,
    detailed: bool = False,
) -> list[str]:
    """個別の引数を受け取ってキューへエントリを追加し、生成ファイル名を返す。  pyfltr: allow-comment

    frontmatterの予約キー（`_RESERVED_FRONTMATTER_KEYS`）以外のキーは入力順で出力frontmatterへ引き継ぐ。
    UWI種別では、`parse_entry_message`が保存形式の未回答外枠を取り除いた本文を保存する。
    残った予約見出し・回答欄マーカーは`WebInputError`で拒否する（CLIとWeb UIの共通処理）。
    `target_repo`を省略（`None`）した場合は、各メッセージのfrontmatterの`target_repo`を必須とし、
    `_wi_sync.repo_lock`取得前に全件の型・非空・解決可否を検証する。
    `submitter_session`はUWI種別のfrontmatterへだけ保存し、回答の通知先としてその会話のセッション状態へファイル名を記録する。
    `target_branch`はfrontmatterに`target_branch`の無い`target_repo`宛ての項目の宛先の系統とする。
    系統を登録したrepository宛てで省略した項目はdefault_branchの系統で保存し、登録に無い系統は保存前に拒否する。
    `initial_state`は保存する状態で、`WI_STATE_HOLD`なら保存した時点から自動処理の対象に入れない。
    `target_commit`は`target_repo`と`target_branch`の解決結果が一致する項目にだけ記録する。
    `detailed`はAWIへ詳細化済みの印（`detailed: true`）を付け、計画を作る時点の詳細化の対象から外す。
    """
    started_at = time.time()
    choices = normalize_choices(choices)
    parsed_messages, normalized_target_repo, normalized_cooldown = _validate_add_entries(
        messages=messages,
        target_repo=target_repo,
        entry_type=entry_type,
        question_type=question_type,
        choices=choices,
        scope=scope,
        target_commit=target_commit,
        source=source,
        cooldown_until=cooldown_until,
    )
    item_branches, target_commit_branch = _resolve_item_branches(parsed_messages, normalized_target_repo, target_branch)
    if draft_basis is not None:
        if target_commit != draft_basis.oid or normalized_target_repo != draft_basis.repo:
            raise WebInputError("起草の基点が保存対象と一致しない", next_action=_DRAFT_NEXT)
        _ensure_draft_destinations(draft_basis, parsed_messages, item_branches, target_commit_branch)
    with (
        _track_batch_report_save(private_notes, entry_type, source, scope, parsed_messages),
        storage.mutation(private_notes) as mutation,
    ):
        if draft_basis is not None:
            draft_basis.verify(target_commit_branch)
        insertions = None
        if resolve_draft_links:
            insertions, _ = _add_drafts.prepare(
                private_notes,
                parsed_messages,
                entry_type=entry_type,
                namespace=draft_namespace,
                timestamp=now.strftime("%Y%m%d-%H%M%S"),
                depends_on=depends_on,
            )
            if not insertions:
                return []
            item_branches = [item_branches[item.position] for item in insertions]
            parsed_messages = [item.message for item in insertions]
        else:
            kept = _add_drafts.legacy_proposal_positions(private_notes, parsed_messages)
            if not kept:
                return []
            item_branches = [item_branches[index] for index in kept]
            parsed_messages = [parsed_messages[index] for index in kept]
        written = add_entries_locked(
            private_notes,
            parsed_messages=parsed_messages,
            target_repo=normalized_target_repo,
            source=source,
            now=now,
            entry_type=entry_type,
            scope=scope,
            question_type=question_type,
            choices=choices,
            target_commit=target_commit,
            depends_on=depends_on,
            cooldown_until=normalized_cooldown,
            submitter_session=submitter_session,
            item_branches=item_branches,
            target_commit_branch=target_commit_branch,
            initial_state=initial_state,
            insertions=insertions,
            detailed=detailed,
        )
        generated = [filename for filename, _content in written]
        count = len(generated)
        mutation.commit(
            f"chore: add {count} {entry_type} {'item' if count == 1 else 'items'}",
            [str(_wi_entries.entry_path(private_notes, filename).relative_to(private_notes)) for filename in generated],
        )
        if saved_details is not None:
            saved_details.update(
                (
                    filename,
                    read_saved_entry_details(_wi_entries.entry_path(private_notes, filename), expected_body=content),
                )
                for filename, content in written
            )
    if (
        submitter_session is not None
        and submitter_session == os.environ.get("CODEX_THREAD_ID")
        and entry_type == WI_TYPE_UWI
        and question_type == "post-approval"
        and initial_state == WI_STATE_INBOX
        and any(_process_loop_session.is_report_opening(body) for _, body in parsed_messages)
        and not _process_loop_session.record_codex_report_save(submitter_session, started_at, os.environ)
    ):
        _outcome.report_warning(
            "巡回の終了判定に使う報告用UWIの保存記録を残せなかった",
            next_action="保存を検収してから、送達と終了操作を行う",
        )
    if submitter_session is not None and entry_type != WI_TYPE_AWI and not _uwi_watch.add(submitter_session, generated):
        _outcome.report_warning(
            f"回答の通知先を記録できなかった: {'、'.join(generated)}",
            next_action="回答の有無は`atk wi show <ファイル名>`で確かめる",
        )
    return generated


def _resolve_item_branches(
    parsed_messages: list[tuple[dict[str, object], str]],
    target_repo: str | None,
    target_branch: str | None,
) -> tuple[list[str | None], str | None]:
    branch_settings = _wi_branches.registry()
    resolved: list[str | None] = []
    for frontmatter, _body in parsed_messages:
        raw_target_repo = frontmatter.get("target_repo")
        item_repo = resolve_repo_id_or_raise(raw_target_repo) if isinstance(raw_target_repo, str) else target_repo
        raw_branch = frontmatter.get(_wi_branches.KEY)
        if raw_branch is not None and (not isinstance(raw_branch, str) or not raw_branch):
            raise WebInputError(
                "target_branchは空でない文字列で指定してください",
                next_action="frontmatterのtarget_branchへ登録した系統の名前を指定する",
            )
        requested = raw_branch if isinstance(raw_branch, str) else target_branch if item_repo == target_repo else None
        assert item_repo is not None
        resolved.append(_wi_branches.resolve_for_save(item_repo, requested, branch_settings))
    commit_branch = (
        _wi_branches.resolve_for_save(target_repo, target_branch, branch_settings) if target_repo is not None else None
    )
    return resolved, commit_branch


def read_body_files(paths: list[str]) -> list[str]:
    """`--body-file`で指定された各パスの内容を本文として読む。

    引用符・改行を含む長文を、シェルの引用規則による解釈を介さずに渡すために使う。
    読み込みに失敗したパスは`WebInputError`を送出し、部分的に読み込んだ内容を投入へ進めない。
    """
    bodies: list[str] = []
    for raw in paths:
        path = pathlib.Path(raw).expanduser()
        try:
            with path.open(encoding="utf-8", newline="") as stream:
                bodies.append(stream.read())
        except OSError as error:
            raise WebInputError(
                f"--body-fileの読み込みに失敗しました: {raw}（{error}）",
                next_action="--body-fileのパスと読み取り権限を確認して再実行する",
            ) from error
        except UnicodeDecodeError as error:
            raise WebInputError(
                f"--body-fileをUTF-8として読めません: {raw}",
                next_action="本文ファイルをUTF-8で保存し直して再実行する",
            ) from error
    return bodies


def _require_process_wi_timing(args: argparse.Namespace, data: dict[str, object]) -> bool:
    timing = getattr(args, "process_wi_timing", None)
    if args.type != WI_TYPE_AWI:
        return False
    in_process_wi = (
        os.environ.get("AGENT_TOOLKIT_PROCESS_LOOP_SESSION") == "1"
        or args.source == "process-wi"
        or data.get("source") == "process-wi"
    )
    next_action = "--process-wi-timing に this-run（今回）か next-run（次回）を指定して再投入する"
    if in_process_wi and timing is None and getattr(args, "hold", False):
        raise WebInputError(
            "process-wi実行中のAWIは--holdで保存できない",
            next_action=f"--holdを外し、{next_action}",
        )
    if in_process_wi and timing is None:
        raise WebInputError("process-wi実行中のAWI投入には対処の時機が必要", next_action=next_action)
    return timing == "this-run"


def _draft_namespace(args: argparse.Namespace) -> str | None:
    explicit = getattr(args, "source_contract", None)
    if explicit is not None:
        path = pathlib.Path(explicit)
        return path.parent.name if path.is_absolute() and path.is_file() else None
    session = _resolve_submitter_session()
    temp = managed_temp.find_session_temp(session) if session is not None else None
    return temp.name if temp is not None and (temp / "work-contract.md").is_file() else None


def _check_external_source_review(
    args: argparse.Namespace, messages: list[str], local_worktree: pathlib.Path | None
) -> str | None:
    explicit = getattr(args, "source_contract", None)
    session = delegated_session.current_conversation_session_id(os.environ)
    temp = managed_temp.find_session_temp(session) if session is not None and explicit is None else None
    contract = pathlib.Path(explicit) if explicit is not None else temp / "work-contract.md" if temp else None
    if explicit is not None and (contract is None or not contract.is_absolute()):
        return "`--source-contract`には作業契約の絶対パスを指定してください。"
    body = ""
    if contract is not None:
        try:
            body = contract.read_text(encoding="utf-8")
        except FileNotFoundError:
            if explicit is not None:
                return f"`{contract}`を読み取れません。"
        except OSError, UnicodeError:
            return f"`{contract}`を読み取れません。"
    recorded = None
    if session is not None and explicit is None:
        entry = read_state(session).get(external_source_review.EXTERNAL_SOURCE_ENTRY_KEY)
        if isinstance(entry, dict):
            recorded = entry.get("urls")
    urls = external_source_review.contract_urls(body, recorded)
    configured_slot = os.environ.get(session_review.WEEKLY_SLOT_ENV, "")
    if configured_slot.startswith("research@"):
        for message in messages:
            frontmatter, _body = parse_entry_message(message, entry_type=args.type)
            if args.source is not None:
                frontmatter = {"source": args.source, **frontmatter}
            metadata = run_report_metadata(frontmatter, question_type=args.question_type, scope=args.scope, new_report=True)
            if metadata.get("weekly_slot") == configured_slot:
                collected = research_sources.collected_source_urls(configured_slot)
                if collected is None:
                    return "週次の資料URLの記録がありません。`atk run-script research-sources` で取得してください。"
                urls = collected
                break
    return external_source_review.check_table(contract, urls, local_worktree or pathlib.Path.cwd())


def cmd_add(
    args: argparse.Namespace,
    private_notes: pathlib.Path,
    now: datetime.datetime,
    home: pathlib.Path,
) -> None:
    """addサブコマンド: メッセージをinboxへ投入してcommit・push。

    対象リポジトリは常にカレントディレクトリから解決する。ただし`atk wi add`直後のトークンが実在
    ディレクトリの場合は旧REPO_PATH位置引数形式の呼び出しとみなし、`atk.py`側の事前抽出で
    その引数をREPO_PATHとして扱う（互換維持、抽出結果は`args.repo_path_override`で受け取る）。
    各メッセージ先頭がYAML frontmatter形式の場合は`target_repo`・`source`をCLIオプションより優先する。
    `--target-repo`指定時は、レガシーREPO_PATH位置引数が無くfrontmatterにも`target_repo`が
    無い場合のfallback値として使う。
    エディター経由の本文確定後に対象worktreeのHEADを取得してから`_wi_sync.pull`を実行する順序とし、
    エディターの起動前に、他端末の投入分を反映するremote同期の完了を投入者が待たずに済むようにする。
    remote同期失敗時はエディターで確定済みの本文をstderrへ再表示してから終了し、入力内容の消失を防ぐ。
    各メッセージの本文が実質空（`body_is_effectively_empty`）の場合は`_wi_sync.repo_lock`取得前に拒否する。
    `--body-file`を指定した場合はそのファイルの内容を本文として扱う。
    引用符・改行を含む長文を、シェルの引用規則による解釈を介さずに渡す。複数回指定すれば複数件を投入できる。
    `--depends-on`が指す依存先が取り込み先に実在しない場合は、投入を拒否せず警告をstderrへ出力する。
    UWIでは投入したセッションの識別子を`submitter_session`へ保存し、その会話へ回答を通知する対象として記録する
    （`_resolve_submitter_session`、`_common/uwi_watch.py`）。
    """
    body_files = getattr(args, "body_file", None)
    hold = bool(getattr(args, "hold", False))
    if body_files:
        try:
            messages = read_body_files(body_files)
        except WebInputError as error:
            _outcome.report_failure(f"投入を拒否した: {error.reason}", next_action=error.next_action)
            sys.exit(1)
    else:
        messages = []
    repo_path_override = args.repo_path_override
    _wi_cli_input.reject_bare_repo_path_override(repo_path_override, messages, args.subparser)
    target_value = repo_path_override if repo_path_override is not None else args.target_repo
    target_repo, local_worktree = resolve_add_target(target_value)
    collected_via_editor = not messages
    if not messages:
        message = _wi_cli_input.collect_message_via_editor()
        if message is None:
            sys.exit(1)
        messages = [message]
    validation_errors: list[WebInputError] = []
    join_current = False
    for message in messages:
        try:
            if is_agent_environment() and _user_comment.has_reserved_heading(message):
                raise WebInputError(
                    _user_comment.AGENT_USER_COMMENT_ADD_ERROR,
                    next_action=_user_comment.AGENT_USER_COMMENT_ADD_NEXT_ACTION,
                )
            frontmatter, body = parse_entry_message(message, entry_type=args.type)
            if not args.dry_run:
                join_current = _require_process_wi_timing(args, frontmatter) or join_current
            _require_agent_source(frontmatter, args.source)
            try:
                require_agent_awi_sections(
                    body,
                    frontmatter,
                    entry_type=args.type,
                    source=args.source,
                )
            except WebInputError as error:
                validation_errors.append(error)
            try:
                require_confirmed_cause(body, entry_type=args.type)
                require_cause_analysis_format(body, entry_type=args.type)
            except WebInputError as error:
                validation_errors.append(error)
        except WebInputError as error:
            if error.reason == _EMPTY_AWI_ERROR:
                preview = message.strip().splitlines()[0] if message.strip() else "(空文字列)"
                validation_errors.append(
                    WebInputError(
                        "投入を拒否した: 本文が実質空である"
                        "（空文字・空白のみ・箇条書きマーカー単独文字のいずれか）。"
                        f"該当メッセージの先頭: {preview}",
                        next_action=_EMPTY_AWI_NEXT_ACTION,
                    )
                )
            else:
                validation_errors.append(error)
    if validation_errors:
        next_actions = dict.fromkeys(error.next_action for error in validation_errors)
        _outcome.report_failure(
            "投入を拒否した: " + "\n".join(error.reason for error in validation_errors),
            next_action="\n".join(next_actions),
        )
        sys.exit(1)
    if args.type == WI_TYPE_UWI and args.depends_on:
        _outcome.report_failure(
            "投入を拒否した: --depends-onは--type=awiでのみ指定できる",
            next_action="--depends-onを外すか、--type=awiで投入し直す",
        )
        sys.exit(1)
    target_branch = getattr(args, "target_branch", None)
    try:
        commit_branch = (
            _wi_branches.resolve_for_save(target_repo, target_branch, _wi_branches.registry()) if target_repo else None
        )
    except WebInputError as error:
        _outcome.report_failure(f"投入を拒否した: {error.reason}", next_action=error.next_action)
        sys.exit(1)
    draft_basis = None
    try:
        draft = getattr(args, "draft_base", None)
        if draft is not None:
            if args.type != WI_TYPE_AWI or local_worktree is None or not isinstance(target_repo, str):
                raise WebInputError(
                    "起草の基点は宛先worktreeを指定したAWIで使う",
                    next_action="--target-repoに宛先のworktreeを指定して、同じ原稿と基点で再実行する",
                )
            draft_basis = DraftBasis(target_repo, local_worktree, draft[0], draft[1])
            draft_basis.verify(commit_branch)
            target_commit = draft_basis.oid
        else:
            target_commit = (
                None
                if local_worktree is None
                else resolve_head_commit(local_worktree)
                if commit_branch is None
                else resolve_head_commit(local_worktree, revision=f"refs/remotes/origin/{commit_branch}")
            )
    except WebInputError as error:
        _outcome.report_failure(f"投入を拒否した: {error.reason}", next_action=error.next_action)
        sys.exit(1)
    except SystemExit:
        if collected_via_editor:
            _outcome.report_failure(
                "HEADコミットの取得に失敗した。確定済みの本文を以下に再表示する",
                next_action="再表示した本文を保存してから再投入する",
            )
            for message in messages:
                print("---", file=sys.stderr)
                print(message, file=sys.stderr)
        raise
    dependency_dir = private_notes / WI_STATE_INBOX
    canonical_dependencies = _normalize_dependencies(args.depends_on, dependency_dir)
    choices = normalize_choices(",".join(args.choices)) if args.choices is not None else None
    saved_details: dict[str, dict[str, object | None]] = {}
    namespace = _draft_namespace(args)
    sources = [str(pathlib.Path(path).expanduser().resolve()) for path in body_files] if body_files else ["エディター入力"]
    try:
        if args.dry_run:
            parsed_messages, normalized_target_repo, _cooldown = _validate_add_entries(
                messages=messages,
                target_repo=target_repo,
                entry_type=args.type,
                question_type=args.question_type,
                choices=choices,
                scope=args.scope,
                target_commit=target_commit,
                source=args.source,
                cooldown_until=getattr(args, "cooldown_until", None),
            )
            item_branches, resolved_branch = _resolve_item_branches(parsed_messages, normalized_target_repo, target_branch)
            if draft_basis is not None:
                _ensure_draft_destinations(draft_basis, parsed_messages, item_branches, resolved_branch)
                draft_basis.verify(resolved_branch)
            _, key_names = _add_drafts.prepare(
                private_notes,
                parsed_messages,
                entry_type=args.type,
                namespace=namespace,
                timestamp=None,
                depends_on=canonical_dependencies,
            )
            print_draft_openings(
                messages,
                sources,
                compare_paths=args.compare_opening_file,
                output_paths=args.opening_output,
                is_uwi=args.type == WI_TYPE_UWI,
            )
            for key, name in key_names.items():
                print(f"  原稿キー: {key} → {name}")
            _outcome.report_success("投入前の検証が成立した（--dry-runのため保存していない）")
            if args.type == WI_TYPE_AWI:
                print_deferral_candidates(messages, sources)
            return
        if args.compare_opening_file is not None:
            if args.type == WI_TYPE_UWI:
                for message in messages:
                    parse_entry_message(message, entry_type=WI_TYPE_UWI)
            compare_draft_openings(messages, sources, compare_paths=args.compare_opening_file, is_uwi=args.type == WI_TYPE_UWI)
        if source_error := _check_external_source_review(args, messages, local_worktree):
            raise WebInputError(
                source_error,
                next_action="作業契約と隣の external-source-review.md を直し、必要なら --source-contract で指定して再実行する",
            )
        generated = add_entries(
            private_notes,
            messages=messages,
            target_repo=target_repo,
            source=args.source,
            now=now,
            entry_type=args.type,
            scope=args.scope,
            question_type=args.question_type,
            choices=choices,
            target_commit=target_commit,
            depends_on=canonical_dependencies,
            cooldown_until=getattr(args, "cooldown_until", None),
            saved_details=saved_details,
            submitter_session=_resolve_submitter_session() if args.type == WI_TYPE_UWI else None,
            target_branch=target_branch,
            initial_state=WI_STATE_HOLD if hold else WI_STATE_INBOX,
            draft_basis=draft_basis,
            draft_namespace=namespace,
            detailed=bool(getattr(args, "detailed", False)),
        )
    except WebInputError as error:
        _outcome.report_failure(f"投入を拒否した: {error.reason}", next_action=error.next_action)
        sys.exit(1)
    except subprocess.CalledProcessError:
        _outcome.report_failure(
            "remote同期に失敗した。確定済みの本文を以下に再表示する",
            next_action=(f"再表示した本文を保存し、`git -C {private_notes} status`で同期状態を確認してから再投入する"),
        )
        for message in messages:
            print("---", file=sys.stderr)
            print(message, file=sys.stderr)
        sys.exit(1)
    count = len(generated)
    inbox_dir = private_notes
    for warning in _missing_dependency_warnings(private_notes, inbox_dir, generated, canonical_dependencies):
        _outcome.report_warning(warning, next_action=MISSING_DEPENDENCY_NEXT_ACTION)
    _outcome.report_success(f"{count}件をholdへ保存した" if hold else f"{count}件をinboxへ投入した")
    for filename in generated:
        print(f"  {shorten_home(_wi_entries.entry_path(private_notes, filename), home)}")
        print_entry_details(saved_details[filename])
    if args.type == WI_TYPE_AWI:
        print_deferral_candidates(messages, sources)
    if join_current:
        print(
            "次の操作: process-wi-additions.mdに従い、pick-wi-check --mergeで選定へ加え、"
            "atk wi start-processingの後に同じ担当へ追送する。",
            file=sys.stderr,
        )


__all__ = [
    "_parse_leading_frontmatter",
    "body_is_effectively_empty",
    "check_run_report_sync",
    "has_committed_run_report",
    "has_synced_run_report",
    "normalize_cooldown_until",
]

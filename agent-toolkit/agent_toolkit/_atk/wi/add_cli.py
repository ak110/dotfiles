import argparse
import contextlib
import datetime
import os
import pathlib
import subprocess
import sys
from collections.abc import Callable

from agent_toolkit._atk import managed_temp
from agent_toolkit._atk import outcome as _outcome
from agent_toolkit._atk.environment import is_agent_environment
from agent_toolkit._atk.wi import add
from agent_toolkit._atk.wi import add_drafts as _add_drafts
from agent_toolkit._atk.wi import branches as _wi_branches
from agent_toolkit._atk.wi import cli_input as _wi_cli_input
from agent_toolkit._atk.wi import entries as _wi_entries
from agent_toolkit._atk.wi import sync as _wi_sync
from agent_toolkit._atk.wi import user_comment as _user_comment
from agent_toolkit._atk.wi.add_reports import (
    compare_draft_openings,
    print_deferral_candidates,
    print_draft_openings,
)
from agent_toolkit._atk.wi.add_validation import (
    _EMPTY_AWI_ERROR,
    _EMPTY_AWI_NEXT_ACTION,
    _require_agent_source,
    _validate_add_entries,
    normalize_choices,
    parse_entry_message,
    require_agent_awi_sections,
    require_cause_analysis_format,
    require_confirmed_cause,
    validate_retrospective_report,
)
from agent_toolkit._atk.wi.constants import (
    QUESTION_TYPE_REPORT,
    WI_STATE_HOLD,
    WI_STATE_INBOX,
    WI_TYPE_AWI,
    WI_TYPE_UWI,
)
from agent_toolkit._atk.wi.filenames import MISSING_DEPENDENCY_NEXT_ACTION
from agent_toolkit._atk.wi.formatters import shorten_home
from agent_toolkit._atk.wi.repo import resolve_repo_id_or_raise
from agent_toolkit._atk.wi.web_input import WebInputError


def _require_process_wi_timing(
    args: argparse.Namespace,
    data: dict[str, object],
    target_repo: str | None,
    matches_submit_bindings: Callable[[object, str], bool],
) -> bool:
    timing = getattr(args, "process_wi_timing", None)
    if args.type != WI_TYPE_AWI:
        return False
    in_process_wi = os.environ.get("AGENT_TOOLKIT_PROCESS_LOOP_SESSION") == "1" or args.source == "process-wi"
    if not in_process_wi and data.get("source") == "process-wi":
        bindings = data.get("wi_submit_bindings")
        if bindings:
            raw_target_repo = data.get("target_repo", target_repo)
            if isinstance(raw_target_repo, str) and matches_submit_bindings(
                bindings, resolve_repo_id_or_raise(raw_target_repo)
            ):
                return False
        in_process_wi = True
    next_action = "--process-wi-timing に this-run（今回）か next-run（次回）を指定して再投入する"
    if in_process_wi and timing is None and getattr(args, "hold", False):
        raise WebInputError(
            "process-wi実行中のAWIは--holdで保存できない",
            next_action=f"--holdを外し、{next_action}",
        )
    if in_process_wi and timing is None:
        raise WebInputError("process-wi実行中のAWI投入には対処の時機が必要", next_action=next_action)
    return timing == "this-run"


def _draft_namespace() -> str | None:
    session = add.resolve_submitter_session()
    temp = managed_temp.find_session_temp(session) if session is not None else None
    return temp.name if temp is not None and (temp / "work-contract.md").is_file() else None


def cmd_add(
    args: argparse.Namespace,
    private_notes: pathlib.Path,
    now: datetime.datetime,
    home: pathlib.Path,
    *,
    matches_submit_bindings: Callable[[object, str], bool],
) -> None:
    body_files = getattr(args, "body_file", None)
    hold = bool(getattr(args, "hold", False))
    report = args.question_type == QUESTION_TYPE_REPORT
    if body_files:
        try:
            messages = add.read_body_files(body_files)
        except WebInputError as error:
            _outcome.report_failure(f"投入を拒否した: {error.reason}", next_action=error.next_action)
            sys.exit(1)
    else:
        messages = []
    repo_path_override = args.repo_path_override
    _wi_cli_input.reject_bare_repo_path_override(repo_path_override, messages, args.subparser)
    target_value = repo_path_override if repo_path_override is not None else args.target_repo
    target_repo, local_worktree = add.resolve_add_target(target_value)
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
            join_current = _require_process_wi_timing(args, frontmatter, target_repo, matches_submit_bindings) or join_current
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
            draft_basis = _add_drafts.DraftBasis(target_repo, local_worktree, draft[0], draft[1])
            draft_basis.verify(commit_branch)
            target_commit = draft_basis.oid
        else:
            target_commit = (
                None
                if local_worktree is None
                else add.resolve_head_commit(local_worktree)
                if commit_branch is None
                else add.resolve_head_commit(local_worktree, revision=f"refs/remotes/origin/{commit_branch}")
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
    canonical_dependencies = add.normalize_dependencies(args.depends_on, dependency_dir)
    choices = normalize_choices(",".join(args.choices)) if args.choices is not None else None
    saved_details: dict[str, dict[str, object | None]] = {}
    namespace = _draft_namespace()
    initial_state = WI_STATE_HOLD if hold else WI_STATE_INBOX
    detailed = bool(getattr(args, "detailed", False))
    sources = [str(pathlib.Path(path).expanduser().resolve()) for path in body_files] if body_files else ["エディター入力"]
    try:
        if args.dry_run:
            parsed_messages, normalized_target_repo, cooldown = _validate_add_entries(
                messages=messages,
                target_repo=target_repo,
                entry_type=args.type,
                question_type=args.question_type,
                choices=choices,
                scope=args.scope,
                target_commit=target_commit,
                source=args.source,
                cooldown_until=getattr(args, "cooldown_until", None),
                origin_reviews=tuple(getattr(args, "origin_review", None) or ()),
            )
            item_branches, resolved_branch = add.resolve_item_branches(parsed_messages, normalized_target_repo, target_branch)
            if args.type == WI_TYPE_UWI and args.question_type == QUESTION_TYPE_REPORT:
                for frontmatter, body in parsed_messages:
                    raw_repo = frontmatter.get("target_repo")
                    item_repo = resolve_repo_id_or_raise(raw_repo) if isinstance(raw_repo, str) else normalized_target_repo
                    validate_retrospective_report(
                        private_notes, body, {"scope": add.item_scope(frontmatter, args.scope), "target_repo": item_repo}
                    )
            if draft_basis is not None:
                _add_drafts.ensure_destinations(draft_basis, parsed_messages, item_branches, resolved_branch)
                draft_basis.verify(resolved_branch)
            preview_retries = (
                namespace is not None
                and args.type == WI_TYPE_AWI
                and any(_add_drafts.KEY not in data and _add_drafts.PROPOSAL_ID_KEY not in data for data, _ in parsed_messages)
            )
            with _wi_sync.repo_lock(private_notes) if preview_retries else contextlib.nullcontext():
                insertions, key_names = _add_drafts.prepare(
                    private_notes,
                    parsed_messages,
                    entry_type=args.type,
                    namespace=namespace,
                    timestamp=now.strftime("%Y%m%d-%H%M%S") if preview_retries else None,
                    depends_on=canonical_dependencies,
                )
                if preview_retries:
                    add.add_entries_locked(
                        private_notes,
                        parsed_messages=[item.message for item in insertions],
                        target_repo=normalized_target_repo,
                        source=args.source,
                        now=now,
                        entry_type=args.type,
                        scope=args.scope,
                        question_type=None,
                        choices=None,
                        item_branches=[item_branches[item.position] for item in insertions],
                        cooldown_until=cooldown,
                        initial_state=initial_state,
                        insertions=insertions,
                        detailed=detailed,
                        draft_namespace=namespace,
                        dry_run=True,
                    )
                    prospective = {item.filename for item in insertions}
                    key_names = {key: "保存時に決まる" if name in prospective else name for key, name in key_names.items()}
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
        generated = add.add_entries(
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
            submitter_session=add.resolve_submitter_session() if args.type == WI_TYPE_UWI else None,
            target_branch=target_branch,
            initial_state=initial_state,
            draft_basis=draft_basis,
            draft_namespace=namespace,
            detailed=detailed,
            origin_reviews=tuple(getattr(args, "origin_review", None) or ()),
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
    for warning in add.missing_dependency_warnings(private_notes, inbox_dir, generated, canonical_dependencies):
        _outcome.report_warning(warning, next_action=MISSING_DEPENDENCY_NEXT_ACTION, source="wi-missing-dependency")
    _outcome.report_success(
        f"{count}件をholdへ保存した" if hold else f"{count}件を報告として保存した" if report else f"{count}件をinboxへ投入した"
    )
    for filename in generated:
        print(f"  {shorten_home(_wi_entries.entry_path(private_notes, filename), home)}")
        add.print_entry_details(saved_details[filename])
    if args.type == WI_TYPE_AWI:
        print_deferral_candidates(messages, sources)
    if join_current:
        print(
            "次の操作: process-wi-additions.mdに従い、pick-wi-check --mergeで選定へ加え、"
            "atk wi start-processingの後に同じ担当へ追送する。",
            file=sys.stderr,
        )

import argparse
import dataclasses
import datetime
import json
import os
import pathlib
import re
import shutil
import sys
from collections.abc import Mapping

from agent_toolkit._atk import config as _config
from agent_toolkit._atk.environment import is_agent_environment
from agent_toolkit._atk.git_sync import has_remote
from agent_toolkit._atk.wi import background_sync
from agent_toolkit._atk.wi import branches as _wi_branches
from agent_toolkit._atk.wi import effect_check as _effect_check
from agent_toolkit._atk.wi import entries as _wi_entries
from agent_toolkit._atk.wi import filters as _wi_filters
from agent_toolkit._atk.wi import priority as _priority
from agent_toolkit._atk.wi import readiness as _wi_readiness
from agent_toolkit._atk.wi import snapshot as _wi_snapshot
from agent_toolkit._atk.wi import sync as _wi_sync
from agent_toolkit._atk.wi import uwi_scan as _wi_uwi_scan
from agent_toolkit._atk.wi.constants import (
    WI_PROCESSABLE_STATES,
    WI_STATE_PROCESSING,
    WI_STATES,
    WI_TYPE_AWI,
    WI_TYPE_UWI,
    WI_TYPES,
)
from agent_toolkit._atk.wi.entry_text import EntryTexts, parse_frontmatter
from agent_toolkit._atk.wi.formatters import (
    NewWorkItem,
    body_summary,
    display_width,
    parse_source,
    parse_target_repo,
    target_repo_budget,
    truncate_target_repo,
    uwi_body_summary,
)
from agent_toolkit._atk.wi.readiness import ReadinessResult
from agent_toolkit._atk.wi.repo import resolve_local_worktree, resolve_repo_id
from agent_toolkit._common.next_action import ActionableError
from agent_toolkit._common.wi_layout import DATED_NAME
from agent_toolkit._git import command as _git_command


@dataclasses.dataclass(frozen=True)
class RepositorySnapshot:
    repository: _config.ConfiguredRepository
    entries: tuple[_wi_entries.EntryRecord, ...]
    readiness: ReadinessResult
    private_notes: pathlib.Path

    @property
    def candidates(self) -> tuple[_wi_entries.EntryRecord, ...]:
        names = {*self.readiness.ready, *self.readiness.internal_dependency_waits}
        return tuple(
            entry
            for entry in self.entries
            if entry[3] in WI_PROCESSABLE_STATES
            and (entry[3] == "processing" or entry[0].name in names)
            and not (entry[4] == WI_TYPE_UWI and not _wi_uwi_scan.is_uwi_answered(entry[2]))
        )

    @property
    def has_work(self) -> bool:
        return bool(self.candidates or self.readiness.actionable_count)

    @property
    def lane_limit(self) -> int:
        return (len(self.candidates) + 9) // 10

    def candidate_records(self) -> list[dict[str, object]]:
        now = datetime.datetime.now(datetime.UTC)
        branch = self.repository.branch
        base = "HEAD" if branch is None else f"refs/remotes/origin/{branch}"
        return [
            {
                "filename": path.name,
                "state": state,
                "type": kind,
                "target_repo": repo_id,
                **({} if branch is None else {"target_branch": branch}),
                "ready": state in WI_PROCESSABLE_STATES and path.name in self.readiness.ready,
                "blocked_reason": self.readiness.blocked_reasons.get(path.name),
                "source": parse_source(text),
                "priority": _priority.priority_of((parse_frontmatter(text) or ({}, ""))[0]),
                "priority_reason": _priority.reason_of(*(parse_frontmatter(text) or ({}, ""))),
                "summary": uwi_body_summary(text, sys.maxsize) if kind == WI_TYPE_UWI else body_summary(text, sys.maxsize),
                "body": text,
                "staleness": _staleness(text, self.repository.worktree, now, base=base),
            }
            for path, repo_id, text, state, kind in self.candidates
        ]


def configured_repo_snapshot(
    private_notes: pathlib.Path,
    repositories: tuple[_config.ConfiguredRepository, ...] | None = None,
    *,
    entries: tuple[_wi_entries.EntryRecord, ...] | None = None,
) -> tuple[RepositorySnapshot, ...]:
    targets = _config.resolve_configured_repositories() if repositories is None else repositories
    if not targets:
        return ()
    snapshot = tuple(_wi_entries.iter_entries(private_notes, WI_STATES, None)) if entries is None else entries

    branch_settings = _wi_branches.registry() if any(repository.branch is not None for repository in targets) else {}
    return tuple(
        RepositorySnapshot(
            repository,
            _configured_entries(snapshot, repository, branch_settings, include_broken=index == 0),
            _wi_readiness.calculate_readiness(
                private_notes,
                repository.repo_id,
                snapshot=snapshot if index == 0 else tuple(entry for entry in snapshot if entry[4] is not None),
                branch_settings=branch_settings,
                target_branch=repository.branch,
            ),
            private_notes,
        )
        for index, repository in enumerate(targets)
    )


def _configured_entries(
    entries: tuple[_wi_entries.EntryRecord, ...],
    repository: _config.ConfiguredRepository,
    branch_settings: _wi_branches.Registry,
    *,
    include_broken: bool,
) -> tuple[_wi_entries.EntryRecord, ...]:
    matcher = _wi_filters.TargetRepoMatcher(repository.repo_id)
    return tuple(
        entry
        for entry in entries
        if (entry[4] is None and include_broken)
        or (
            matcher.matches(entry[1])
            and (
                repository.branch is None
                or _wi_branches.matches(entry[2], repository.repo_id, repository.branch, branch_settings)
            )
        )
    )


def _state_readiness(state: str, filename: str, readiness: ReadinessResult) -> str:
    if state not in WI_PROCESSABLE_STATES:
        return "complete"
    return "ready" if filename in readiness.ready else "blocked"


def print_entries(
    selected: list[_wi_entries.EntryRecord],
    readiness: ReadinessResult,
    branch_settings: _wi_branches.Registry | None = None,
) -> None:
    for header_type in (*WI_TYPES, None):
        group = [entry for entry in selected if entry[4] == header_type]
        if not group:
            continue
        print(f"# {header_type or 'unknown'}")
        for path, target_repo, text, state, entry_type in sorted(group, key=lambda entry: entry[0].name):
            parsed = parse_frontmatter(text)
            # `plan`区分は、廃止した計画ファイル付きの型で保存された終了済み項目を表示で見分けるための読取互換である。
            plan_file = parsed[0].get("plan_file") if parsed is not None else None
            item_kind = "frontmatter-broken" if parsed is None else "plan" if isinstance(plan_file, str) else "normal"
            state_readiness = _state_readiness(state, path.name, readiness)
            label = f"{state}/{item_kind}/{state_readiness}"
            answered = False
            if entry_type == WI_TYPE_UWI and _wi_uwi_scan.is_report(text):
                label = f"{state}/report"
            elif entry_type == WI_TYPE_UWI:
                answered = _wi_uwi_scan.is_uwi_answered(text)
                label = (
                    f"{state}/answered/blocked"
                    if answered and state_readiness == "blocked"
                    else f"{state}/answered"
                    if answered
                    else f"{state}/unanswered"
                )

            if sys.stdout.isatty():
                repo_budget = target_repo_budget(path.name, label)
                display_repo = truncate_target_repo(target_repo, max_width=repo_budget)
            else:
                display_repo = target_repo
            reason = (
                readiness.blocked_reasons.get(path.name)
                if state_readiness == "blocked" and (entry_type != WI_TYPE_UWI or answered)
                else None
            )
            reason_suffix = f" blocked_reason={reason}" if reason is not None and reason != "frontmatter-broken" else ""
            branch = _wi_branches.effective(text, target_repo, branch_settings or {})
            if branch is not None:
                reason_suffix = f" target_branch={branch}{reason_suffix}"
            if reason == "cooldown-until":
                cooldown_until = dict(readiness.cooldown_values)[path.name]
                reason_suffix += f" cooldown_until={cooldown_until}"
            if parsed is not None and parsed[0].get("effect_check"):
                reason_suffix += f" effect_check={parsed[0]['effect_check']}"
                effect_reason = _effect_check.pending_reason(text)
                if effect_reason:
                    reason_suffix += f" effect_check_reason={effect_reason}"
            prefix = f"{path.name}: {display_repo} [{label}]{reason_suffix} "
            available_width = shutil.get_terminal_size().columns - display_width(prefix) if sys.stdout.isatty() else sys.maxsize
            summary = (
                uwi_body_summary(text, available_width) if entry_type == WI_TYPE_UWI else body_summary(text, available_width)
            )
            print(f"{prefix}{summary}")


def _staleness(
    text: str, local_worktree: pathlib.Path | None, now: datetime.datetime, *, base: str = "HEAD"
) -> dict[str, object]:
    """target_commit以後に12時間以上経過したcommitがあるかを返す。

    `base`は比較先のrevisionで、系統を持つ項目ではその系統のリモートブランチを渡す。

    履歴は項目の`target_repo`に対応するローカル作業ツリーで調べる。
    frontmatterの`target_repo`は正規化リモートURLであり`git -C`へ渡せないため、引数を作業ツリーのパスに限る。
    対応する作業ツリーが無い場合は`git`を起動せず`local-worktree-unavailable`を返し、
    作業ツリーで`git rev-list`が失敗した場合（`target_commit`が履歴に無い場合など）の`history-unavailable`と区別する。
    """
    parsed = parse_frontmatter(text)
    target_commit = parsed[0].get("target_commit") if parsed is not None else None
    if not isinstance(target_commit, str) or not target_commit:
        return {"status": "indeterminate", "reason": "target-commit-missing"}
    if local_worktree is None:
        return {"status": "indeterminate", "reason": "local-worktree-unavailable"}
    result = _git_command.run(
        ["-C", str(local_worktree), "rev-list", "--format=%ct", "--no-commit-header", f"{target_commit}..{base}"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return {"status": "indeterminate", "reason": "history-unavailable"}
    timestamps = [int(line) for line in result.stdout.splitlines() if line.isdigit()]
    cutoff = int((now - datetime.timedelta(hours=12)).timestamp())
    old_count = sum(timestamp <= cutoff for timestamp in timestamps)
    if old_count:
        return {"status": "notice", "old_commit_count": old_count, "later_commit_count": len(timestamps)}
    return {"status": "current", "later_commit_count": len(timestamps)}


def _print_json_entries(
    selected: list[_wi_entries.EntryRecord],
    readiness: ReadinessResult,
    *,
    include_staleness: bool = False,
    staleness_now: datetime.datetime | None = None,
    local_worktrees: Mapping[str, pathlib.Path] | None = None,
    branch_settings: _wi_branches.Registry | None = None,
) -> None:
    now = staleness_now or datetime.datetime.now(datetime.UTC)
    for path, target_repo, text, state, entry_type in sorted(selected, key=lambda entry: entry[0].name):
        state_readiness = _state_readiness(state, path.name, readiness)
        answered = entry_type == WI_TYPE_UWI and _wi_uwi_scan.is_uwi_answered(text)
        reason = (
            readiness.blocked_reasons.get(path.name)
            if state_readiness == "blocked" and (entry_type != WI_TYPE_UWI or answered)
            else None
        )
        summary = uwi_body_summary(text, sys.maxsize) if entry_type == WI_TYPE_UWI else body_summary(text, sys.maxsize)
        metadata, body = parse_frontmatter(text) or ({}, "")
        branch = _wi_branches.effective(text, target_repo, branch_settings or {})
        record: dict[str, object] = {
            "filename": path.name,
            "type": entry_type,
            "target_repo": target_repo,
            **({} if branch is None else {"target_branch": branch}),
            "state": state,
            "ready": state in WI_PROCESSABLE_STATES and path.name in readiness.ready,
            "blocked_reason": reason,
            "source": parse_source(text),
            "important": _wi_filters.is_important(text),
            "priority": _priority.priority_of(metadata),
            "priority_reason": _priority.reason_of(metadata, body),
            "effect_check": (parse_frontmatter(text) or ({}, ""))[0].get("effect_check"),
            "effect_check_reason": _effect_check.pending_reason(text),
            "summary": summary,
        }
        if include_staleness:
            record["staleness"] = _staleness(
                text,
                (local_worktrees or {}).get(target_repo),
                now,
                base="HEAD" if branch is None else f"refs/remotes/origin/{branch}",
            )
        print(json.dumps(record, ensure_ascii=False, separators=(",", ":")))


def _print_summary_entries(selected: list[_wi_entries.EntryRecord]) -> None:
    for path, _, text, _, entry_type in sorted(selected, key=lambda entry: entry[0].name):
        summary = uwi_body_summary(text, sys.maxsize) if entry_type == WI_TYPE_UWI else body_summary(text, sys.maxsize)
        print(json.dumps({"filename": path.name, "summary": summary}, ensure_ascii=False, separators=(",", ":")))


def _local_worktrees(args: argparse.Namespace, target_values: list[str]) -> dict[str, pathlib.Path]:
    worktrees: dict[str, pathlib.Path] = {}
    if getattr(args, "target_repo_defaulted", False):
        for value in target_values:
            worktrees.setdefault(value, resolve_local_worktree(None))
        return worktrees
    for value in target_values:
        if pathlib.Path(value).expanduser().exists():
            worktrees.setdefault(resolve_repo_id(value), resolve_local_worktree(value))
    return worktrees


def _staleness_paths(private_notes: pathlib.Path, filenames: list[str]) -> list[pathlib.Path]:
    paths = []
    for name in dict.fromkeys(filenames):
        if DATED_NAME.fullmatch(name) is None:
            raise ActionableError(
                f"鮮度照会のWIファイル名が不正: {name}",
                next_action="YYYYMMDD-HHMMSS-NNN.md形式のファイル名を指定して再実行する",
            )
        dated = _wi_entries.entry_path(private_notes, name)
        candidates = (dated, *(private_notes / state / name for state in WI_STATES))
        path = next((candidate for candidate in candidates if candidate.is_file()), None)
        if path is None:
            raise ActionableError(
                f"指定されたWIが見つからない: {name}",
                next_action="ファイル名と同期状態を確かめ、対象のWI名を指定して再実行してください",
            )
        if not path.resolve().is_relative_to(private_notes.resolve()):
            raise ActionableError(
                f"指定されたWIが管理領域の外を指す: {name}",
                next_action="対象のWIを正規の保存先へ戻して再実行してください",
            )
        paths.append(path)
    return paths


def _validate_named_staleness_options(args: argparse.Namespace) -> None:
    """指定名照会で使わない一覧条件を同期前に拒む。"""
    if (
        any(
            getattr(args, option, None)
            for option in (
                "configured_repos",
                "snapshot_output",
                "new_since",
                "count",
                "summary_only",
                "jsonl",
                "no_jsonl",
                "with_staleness",
                "target_branch",
                "effect_check",
                "source",
            )
        )
        or args.type != ["all"]
        or args.answered != ["all"]
        or args.important != "all"
        or getattr(args, "user_comment", "all") != "all"
    ):
        args.subparser.error("--staleness-forでは--state・--target-repo・同期以外の一覧条件と出力は省略値にしてください。")


def _print_named_staleness(args: argparse.Namespace, private_notes: pathlib.Path, filenames: list[str]) -> None:
    targets = args.target_repo or []
    matcher = _wi_filters.TargetRepoMatcher([resolve_repo_id(target) for target in targets] or None)
    worktrees = _local_worktrees(args, targets)
    branches = _wi_branches.registry()
    states = _wi_filters.resolve_states(args.status)
    now = datetime.datetime.now(datetime.UTC)
    entries = []
    texts = EntryTexts()
    for path in _staleness_paths(private_notes, filenames):
        try:
            text = texts.read(path)
        except OSError as error:
            raise ActionableError(
                f"指定されたWIを読めない: {path.name} ({error})",
                next_action="対象の保存先と読取権限を確かめて再実行する",
            ) from error
        repo = parse_target_repo(text)
        if not matcher.matches(repo):
            raise ActionableError(
                f"指定されたWIの対象リポジトリが一致しない: {path.name}",
                next_action="WI名と対象作業ツリーを確かめて再実行する",
            )
        state = "inbox" if parse_frontmatter(text) is None else _wi_entries.entry_state_of(path, text)
        if state not in states:
            continue
        branch = _wi_branches.effective(text, repo, branches)
        entries.append((path.name, text, worktrees.get(repo), "HEAD" if branch is None else f"refs/remotes/origin/{branch}"))
    for name, text, worktree, base in entries:
        print(json.dumps({"filename": name, "staleness": _staleness(text, worktree, now, base=base)}, ensure_ascii=False))


def cmd_list(args: argparse.Namespace, private_notes: pathlib.Path) -> None:
    filenames = getattr(args, "staleness_for", None)
    if filenames:
        _validate_named_staleness_options(args)
    baseline = _wi_snapshot.read(args.new_since) if getattr(args, "new_since", None) is not None else None
    background_sync.prepare_read(private_notes, skip=args.skip_pull, force=getattr(args, "pull", False))
    if filenames:
        _print_named_staleness(args, private_notes, filenames)
        return
    texts = EntryTexts()
    all_entries = tuple(_wi_entries.iter_entries(private_notes, WI_STATES, None, texts=texts, indexed=True))
    configured = getattr(args, "configured_repos", False)
    repositories = _config.resolve_configured_repositories() if configured else None

    def configured_start() -> tuple[list[tuple[str, str | None]], frozenset[_wi_snapshot.Identifier]]:
        if not configured and os.environ.get("AGENT_TOOLKIT_PROCESS_LOOP_SESSION") != "1":
            values = args.target_repo if isinstance(args.target_repo, list) else [args.target_repo] if args.target_repo else []
            repos = tuple(dict.fromkeys(resolve_repo_id(repo) for repo in values))
            branch = _wi_branches.requested_filter(args, repos, _wi_branches.registry())
            return [(repo, branch) for repo in repos], frozenset()
        selected = _config.resolve_configured_repositories() if repositories is None else repositories
        branches = _wi_branches.registry() if any(repo.branch is not None for repo in selected) else {}
        return (
            [(repo.repo_id, repo.branch) for repo in selected],
            _wi_snapshot.identifiers(
                entry
                for index, repo in enumerate(selected)
                for entry in _configured_entries(all_entries, repo, branches, include_broken=index == 0)
            ),
        )

    output = getattr(args, "snapshot_output", None)
    recorded = _wi_snapshot.record_list_start(
        private_notes,
        all_entries,
        configured_start=configured_start,
        candidate_list=baseline is None and "processable" in args.status,
        output=output,
        configured_output=configured,
    )
    if configured:
        snapshots = configured_repo_snapshot(private_notes, repositories, entries=all_entries)
        configured_branches = _wi_branches.registry()
        if output is not None and not recorded:
            _wi_snapshot.save(output, (entry for item in snapshots for entry in item.entries))
        total = 0
        for snapshot in snapshots:
            states = _wi_filters.resolve_states(args.status)
            kinds = {args.type} if isinstance(args.type, str) else set(args.type)
            selected = [
                entry
                for entry in (
                    _wi_snapshot.new_entries(snapshot.entries, baseline) if baseline is not None else snapshot.entries
                )
                if entry[3] in states
                and ("all" in kinds or entry[4] in kinds)
                and _wi_filters.answered_matches(entry[4], entry[2], args.answered)
                and _wi_filters.source_matches_any(parse_source(entry[2]), args.source)
                and _wi_filters.important_matches(entry[2], getattr(args, "important", "all"))
                and _wi_filters.user_comment_matches(entry[2], getattr(args, "user_comment", "all"))
                and (
                    getattr(args, "effect_check", None) is None
                    or (parse_frontmatter(entry[2]) or ({}, ""))[0].get("effect_check") == args.effect_check
                )
            ]
            total += len(selected)
            if args.count:
                continue
            if args.summary_only:
                _print_summary_entries(selected)
            elif getattr(args, "jsonl", False) or (is_agent_environment() and not getattr(args, "no_jsonl", False)):
                _print_json_entries(
                    selected,
                    snapshot.readiness,
                    include_staleness=getattr(args, "with_staleness", False),
                    local_worktrees={snapshot.repository.repo_id: snapshot.repository.worktree},
                    branch_settings=configured_branches,
                )
            else:
                print_entries(selected, snapshot.readiness, configured_branches)
        if args.count:
            print(total)
        return
    target_values = args.target_repo if isinstance(args.target_repo, list) else [args.target_repo] if args.target_repo else []
    resolved_repos = tuple(dict.fromkeys(resolve_repo_id(repo) for repo in target_values))
    readiness_target = resolved_repos[0] if len(resolved_repos) == 1 else None
    branch_settings = _wi_branches.registry()
    target_branch = _wi_branches.requested_filter(args, resolved_repos, branch_settings)
    if output is not None and not recorded:
        _wi_snapshot.save(output, all_entries)
    readiness = _wi_readiness.calculate_readiness(
        private_notes,
        readiness_target,
        texts=texts,
        snapshot=all_entries,
        branch_settings=branch_settings,
        target_branch=target_branch if readiness_target is not None else None,
    )

    selected = _wi_entries.select_entries(
        private_notes,
        status=args.status,
        target_repo=resolved_repos or None,
        entry_type=args.type,
        answered=args.answered,
        source=args.source,
        texts=texts,
        important=getattr(args, "important", "all"),
        snapshot=_wi_snapshot.new_entries(all_entries, baseline) if baseline is not None else all_entries,
        target_branch=target_branch,
        branch_settings=branch_settings,
        user_comment=getattr(args, "user_comment", "all"),
    )
    if getattr(args, "effect_check", None) is not None:
        selected = [
            entry for entry in selected if (parse_frontmatter(entry[2]) or ({}, ""))[0].get("effect_check") == args.effect_check
        ]

    if args.count:
        print(len(selected))
        return

    if args.summary_only:
        _print_summary_entries(selected)
        return

    if getattr(args, "jsonl", False) or (is_agent_environment() and not getattr(args, "no_jsonl", False)):
        include_staleness = getattr(args, "with_staleness", False)
        _print_json_entries(
            selected,
            readiness,
            include_staleness=include_staleness,
            local_worktrees=_local_worktrees(args, target_values) if include_staleness else None,
            branch_settings=branch_settings,
        )
        return

    print_entries(selected, readiness, branch_settings)


def progress_new_wis(
    session_id: str, *, targets: frozenset[tuple[str, str | None]] | None = None, track_merges: bool = False
) -> tuple[NewWorkItem, ...] | None:
    if not (saved := _wi_snapshot.progress_sources(session_id)):
        return None
    by_key = {(repo.repo_id, repo.branch): repo for repo in _config.resolve_configured_repositories()}
    scopes = {(row["repo"], row["branch"]): row for row in saved.values()}
    if targets is not None:
        selected = {}
        for repo, branch in targets:
            matched = {
                scope: row
                for scope, row in scopes.items()
                if scope[0] == repo and (branch is None or scope[1] in (None, branch))
            }
            if not matched:
                return None
            selected.update(matched)
        scopes = selected
    notes = {row["notes"] for row in scopes.values()}
    if len(notes) != 1:
        raise ValueError("開始時の対象と現在の登録が一致しない")
    comparisons = {}
    for scope, row in scopes.items():
        current = {key for key in by_key if key[0] == scope[0] and (scope[1] is None or key[1] == scope[1])}
        if not current:
            registered = sorted(f"{repo} / {branch or '系統なし'}" for repo, branch in by_key if repo == scope[0])
            raise ActionableError(
                f"開始時の対象に対応する現在の登録がない: {scope[0]} / {scope[1] or '系統なし'}。"
                f"現在の登録: {', '.join(registered) or 'なし'}。",
                next_action=(
                    "atk config repos listで登録を確認するか、元の開始時集合を使って"
                    "同じリポジトリ・系統のWIを手動照合してください。"
                    "開始時集合を現在の集合で置き換えないでください。"
                ),
            )
        for key in current:
            if targets is None or any(
                repo == key[0] and (branch is None or branch == key[1] or (scope[1] is None and (repo, branch) not in by_key))
                for repo, branch in targets
            ):
                comparisons[key] = row
    private_notes = pathlib.Path(notes.pop())
    if not private_notes.is_dir():
        raise ValueError("private-notesを読めない")
    baselines = {
        scope: _wi_snapshot.read(pathlib.Path(row["path"]), expected_digest=row["digest"])
        - {tuple(identifier) for identifier in row.get("unanswered", ())}
        for scope, row in comparisons.items()
    }
    rows: dict[str, NewWorkItem] = {}
    with _wi_sync.repo_lock(private_notes, timeout=0):
        _wi_sync.ensure_mutation_allowed(private_notes)
        # 点検は操作を止めないよう待機を制限し、分岐時のrebaseと旧形式移行は通常の一覧へ任せる。  # pyfltr: allow-comment
        with _git_command.timeout_scope(8.0):
            if has_remote(private_notes):
                _wi_sync.run_git(["fetch"], private_notes, forward_error_output=False)
                _wi_sync.run_git(["merge", "--ff-only", "@{u}"], private_notes, forward_error_output=False)
            elif _git_command.run(["remote"], cwd=private_notes, capture_output=True, check=True).stdout.strip():
                raise ValueError("private-notesの同期先が設定されていない")
        snapshots = configured_repo_snapshot(private_notes, tuple(by_key[scope] for scope in comparisons))
        started = _wi_snapshot.processing_started(session_id, private_notes)
        for snapshot in snapshots:
            repo = snapshot.repository
            baseline = baselines[repo.repo_id, repo.branch]
            for path, _repo, body, state, kind in _wi_snapshot.new_entries(snapshot.candidates, baseline):
                source = parse_source(body) or "未指定"
                if source != "未指定" and not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", source):
                    source = "その他"
                branch = (parse_frontmatter(body) or ({}, ""))[0].get("merge_branch")
                own = state == WI_STATE_PROCESSING and path.name in started
                merge_pending = (
                    state
                    if track_merges
                    and kind == WI_TYPE_AWI
                    and source == "implement"
                    and branch
                    and path.name in snapshot.readiness.ready
                    and (own or state != WI_STATE_PROCESSING)
                    else None
                )
                if own and merge_pending is None:
                    continue
                rows[path.name] = NewWorkItem(path.name, source, bool(branch), merge_pending)
    return tuple(rows[name] for name in sorted(rows))

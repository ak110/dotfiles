"""`atk wi list`による一覧の表示（テキスト・JSON Lines・件数・要約）と着手可否の表示。"""

import argparse
import dataclasses
import datetime
import json
import pathlib
import shutil
import sys
from collections.abc import Mapping

from agent_toolkit._atk import config as _config
from agent_toolkit._atk.environment import is_agent_environment
from agent_toolkit._atk.wi import entries as _wi_entries
from agent_toolkit._atk.wi import filters as _wi_filters
from agent_toolkit._atk.wi import readiness as _wi_readiness
from agent_toolkit._atk.wi import sync as _wi_sync
from agent_toolkit._atk.wi import uwi_scan as _wi_uwi_scan
from agent_toolkit._atk.wi.constants import WI_PROCESSABLE_STATES, WI_STATES, WI_TYPE_UWI, WI_TYPES
from agent_toolkit._atk.wi.entry_text import EntryTexts, parse_frontmatter
from agent_toolkit._atk.wi.formatters import (
    body_summary,
    display_width,
    parse_source,
    target_repo_budget,
    truncate_target_repo,
    uwi_body_summary,
)
from agent_toolkit._atk.wi.readiness import ReadinessResult
from agent_toolkit._atk.wi.repo import resolve_local_worktree, resolve_repo_id
from agent_toolkit._common import automated_prompt as _automated_prompt
from agent_toolkit._git import command as _git_command


@dataclasses.dataclass(frozen=True)
class RepositorySnapshot:
    """1回のキュー取得から得た登録先の候補・診断と処理上限。"""

    repository: _config.ConfiguredRepository
    entries: tuple[_wi_entries.EntryRecord, ...]
    readiness: ReadinessResult
    private_notes: pathlib.Path

    @property
    def candidates(self) -> tuple[_wi_entries.EntryRecord, ...]:
        """processing全件と、readyまたは内部依存待ちのinboxを返す。未回答のUWIと`run-skill`由来のUWIは除く。"""
        names = {*self.readiness.ready, *self.readiness.internal_dependency_waits}
        return tuple(
            entry
            for entry in self.entries
            if entry[3] in WI_PROCESSABLE_STATES
            and (entry[3] == "processing" or entry[0].name in names)
            and not (
                entry[4] == WI_TYPE_UWI
                and (parse_source(entry[2]) == _automated_prompt.SOURCE_RUN_SKILL or not _wi_uwi_scan.is_uwi_answered(entry[2]))
            )
        )

    @property
    def has_work(self) -> bool:
        """候補か対処を要する診断があり、担当の起動が必要かを返す。"""
        return bool(self.candidates or self.readiness.actionable_count)

    @property
    def lane_limit(self) -> int:
        """候補数に対するリポジトリ別のレーン上限を返す。"""
        return (len(self.candidates) + 9) // 10

    def candidate_records(self) -> list[dict[str, object]]:
        """担当へ渡す本文と鮮度を同じ取得済み候補から返す。"""
        now = datetime.datetime.now(datetime.UTC)
        return [
            {
                "filename": path.name,
                "state": state,
                "type": kind,
                "target_repo": repo_id,
                "ready": state in WI_PROCESSABLE_STATES and path.name in self.readiness.ready,
                "blocked_reason": _blocked_reason(self.readiness, path.name) if path.name in self.readiness.blocked else None,
                "source": parse_source(text),
                "summary": uwi_body_summary(text, sys.maxsize) if kind == WI_TYPE_UWI else body_summary(text, sys.maxsize),
                "body": text,
                "staleness": _staleness(text, self.repository.worktree, now),
            }
            for path, repo_id, text, state, kind in self.candidates
        ]


def configured_repo_snapshot(
    private_notes: pathlib.Path,
    repositories: tuple[_config.ConfiguredRepository, ...] | None = None,
) -> tuple[RepositorySnapshot, ...]:
    """登録集合を固定し、一括読込したキューを対象ごとの依存判定へ渡す。"""
    targets = _config.resolve_configured_repositories() if repositories is None else repositories
    if not targets:
        return ()
    snapshot = tuple(_wi_entries.iter_entries(private_notes, WI_STATES, None))
    return tuple(
        RepositorySnapshot(
            repository,
            tuple(
                entry
                for entry in snapshot
                if (entry[4] is None and index == 0) or _wi_filters.TargetRepoMatcher(repository.repo_id).matches(entry[1])
            ),
            _wi_readiness.calculate_readiness(
                private_notes,
                repository.repo_id,
                snapshot=snapshot if index == 0 else tuple(entry for entry in snapshot if entry[4] is not None),
            ),
            private_notes,
        )
        for index, repository in enumerate(targets)
    )


def _state_readiness(state: str, filename: str, readiness: ReadinessResult) -> str:
    """一覧表示用の状態別着手可否を返す。"""
    if state not in WI_PROCESSABLE_STATES:
        return "complete"
    return "ready" if filename in readiness.ready else "blocked"


def _blocked_reason(readiness: ReadinessResult, filename: str) -> str | None:
    """項目の具体的なblocked理由を安定した識別子で返す。

    依存の未充足は、未終了の依存先が全て`--target-repo`の`processable`集合の内側にあるかで
    2値へ分ける。未終了の依存先を持たないblockedは、その集合の内側にある項目が終了しても解除されないため
    外側として返す。この対応が一致しなくなると、時間経過だけで解除される待機が内側として返り、
    そのprocess-wiの実行で着手できない項目が選定の候補へ入る。
    """
    reasons = (
        ("frontmatter-broken", readiness.frontmatter_broken),
        ("invalid-cooldown", readiness.invalid_cooldowns),
        ("invalid-dependency", readiness.invalid_dependencies),
        ("missing-dependency", readiness.missing_dependencies),
        ("self-dependency", readiness.self_dependencies),
        ("cyclic-dependency", readiness.cyclic_dependencies),
        ("cooldown-until", readiness.cooldown_pending),
    )
    specific = next((reason for reason, filenames in reasons if filename in filenames), None)
    if specific is not None:
        return specific
    if filename not in readiness.blocked:
        return None
    return "dependency-unmet-internal" if filename in readiness.internal_dependency_waits else "dependency-unmet-external"


def print_entries(selected: list[_wi_entries.EntryRecord], readiness: ReadinessResult) -> None:
    """選択済みエントリを`atk wi list`の1件1行形式で出力する。"""
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
            if entry_type == WI_TYPE_UWI:
                answered = _wi_uwi_scan.is_uwi_answered(text)
                label = (
                    f"{state}/answered/blocked"
                    if answered and state_readiness == "blocked"
                    else f"{state}/answered"
                    if answered
                    else f"{state}/unanswered"
                )
            # パイプ・リダイレクトでは後段が機械的に本文を取得できるよう短縮しない。
            if sys.stdout.isatty():
                repo_budget = target_repo_budget(path.name, label)
                display_repo = truncate_target_repo(target_repo, max_width=repo_budget)
            else:
                display_repo = target_repo
            reason = (
                _blocked_reason(readiness, path.name)
                if state_readiness == "blocked" and (entry_type != WI_TYPE_UWI or answered)
                else None
            )
            reason_suffix = f" blocked_reason={reason}" if reason is not None and reason != "frontmatter-broken" else ""
            if reason == "cooldown-until":
                cooldown_until = dict(readiness.cooldown_values)[path.name]
                reason_suffix += f" cooldown_until={cooldown_until}"
            prefix = f"{path.name}: {display_repo} [{label}]{reason_suffix} "
            available_width = shutil.get_terminal_size().columns - display_width(prefix) if sys.stdout.isatty() else sys.maxsize
            summary = (
                uwi_body_summary(text, available_width) if entry_type == WI_TYPE_UWI else body_summary(text, available_width)
            )
            print(f"{prefix}{summary}")


def _staleness(text: str, local_worktree: pathlib.Path | None, now: datetime.datetime) -> dict[str, object]:
    """target_commit以後に12時間以上経過したcommitがあるかを返す。

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
        ["-C", str(local_worktree), "rev-list", "--format=%ct", "--no-commit-header", f"{target_commit}..HEAD"],
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
) -> None:
    """選択済みエントリを端末幅によらず同じ形のJSON Linesで出力する。

    `local_worktrees`はリポジトリ識別子からローカル作業ツリーへの対応で、鮮度の判定に使う。
    """
    now = staleness_now or datetime.datetime.now(datetime.UTC)
    for path, target_repo, text, state, entry_type in sorted(selected, key=lambda entry: entry[0].name):
        state_readiness = _state_readiness(state, path.name, readiness)
        answered = entry_type == WI_TYPE_UWI and _wi_uwi_scan.is_uwi_answered(text)
        reason = (
            _blocked_reason(readiness, path.name)
            if state_readiness == "blocked" and (entry_type != WI_TYPE_UWI or answered)
            else None
        )
        summary = uwi_body_summary(text, sys.maxsize) if entry_type == WI_TYPE_UWI else body_summary(text, sys.maxsize)
        record: dict[str, object] = {
            "filename": path.name,
            "type": entry_type,
            "target_repo": target_repo,
            "state": state,
            "ready": state in WI_PROCESSABLE_STATES and path.name in readiness.ready,
            "blocked_reason": reason,
            "source": parse_source(text),
            "important": _wi_filters.is_important(text),
            "effect_check": (parse_frontmatter(text) or ({}, ""))[0].get("effect_check"),
            "summary": summary,
        }
        if include_staleness:
            record["staleness"] = _staleness(text, (local_worktrees or {}).get(target_repo), now)
        print(json.dumps(record, ensure_ascii=False, separators=(",", ":")))


def _print_summary_entries(selected: list[_wi_entries.EntryRecord]) -> None:
    """選択済みエントリのファイル名と要約をJSON Linesで出力する。"""
    for path, _, text, _, entry_type in sorted(selected, key=lambda entry: entry[0].name):
        summary = uwi_body_summary(text, sys.maxsize) if entry_type == WI_TYPE_UWI else body_summary(text, sys.maxsize)
        print(json.dumps({"filename": path.name, "summary": summary}, ensure_ascii=False, separators=(",", ":")))


def _local_worktrees(args: argparse.Namespace, target_values: list[str]) -> dict[str, pathlib.Path]:
    """`--target-repo`の値のうちローカル作業ツリーを指すものを、リポジトリ識別子と対応付けて返す。

    実在するローカルパスの指定はそのパスを、省略時に現在位置から補った識別子は現在位置の作業ツリーを使う。
    正規化リモートURLでの指定と`all`は作業ツリーを持たないため対応に含めない。
    """
    worktrees: dict[str, pathlib.Path] = {}
    if getattr(args, "target_repo_defaulted", False):
        for value in target_values:
            worktrees.setdefault(value, resolve_local_worktree(None))
        return worktrees
    for value in target_values:
        if pathlib.Path(value).expanduser().exists():
            worktrees.setdefault(resolve_repo_id(value), resolve_local_worktree(value))
    return worktrees


def cmd_list(args: argparse.Namespace, private_notes: pathlib.Path) -> None:
    """`list`サブコマンド: AWI/`uwi`を1件1行（ファイル名・`target_repo`・状態・要約）で出力する。

    `--type`指定で出力対象種別（awi・uwi・all）を限定する（省略時はall）。
    `--state`指定で表示範囲を限定する（省略時はactive）。
    `active`は`inbox`・`processing`・`hold`、`processable`は`inbox`・`processing`を出力する。
    個別状態は`inbox`・`processing`・`hold`・`adopted`・`rejected`を解釈し、`all`は5状態すべてを出力する。
    `--answered`（`all`・`yes`・`no`）でUWIの回答状況を限定し、`yes`・`no`指定時はAWIを除外する。
    `--source`指定時はAWI・`uwi`双方をfrontmatterの`source`一致（`!`接頭で否定、無指定エントリも対象に含む）へ限定する。
    `--target-repo`指定時は、正規化リモートURLへ変換した値とfrontmatterの`target_repo`が
    完全一致するエントリのみを出力する。
    出力はAWI・`uwi`の種別でグループ化し、各グループ内を状態によらずファイル名の昇順で整列する。
    該当エントリが1件以上ある種別だけ見出しを出力する。
    `--count`指定時は、フィルター適用後のAWI件数とUWI件数の合計を整数のみで出力し、
    種別見出し・エントリ行は出力しない。
    `--summary-only`指定時は、ファイル名と要約だけをJSON Linesで出力する。
    """
    if not args.skip_pull:
        with _wi_sync.repo_lock(private_notes):
            _wi_sync.pull_with_recent_reuse(private_notes, force_pull=getattr(args, "pull", False))
    if getattr(args, "configured_repos", False):
        snapshots = configured_repo_snapshot(private_notes)
        total = 0
        for snapshot in snapshots:
            states = _wi_filters.resolve_states(args.status)
            kinds = {args.type} if isinstance(args.type, str) else set(args.type)
            selected = [
                entry
                for entry in snapshot.entries
                if entry[3] in states
                and ("all" in kinds or entry[4] in kinds)
                and _wi_filters.answered_matches(entry[4], entry[2], args.answered)
                and _wi_filters.source_matches_any(parse_source(entry[2]), args.source)
                and _wi_filters.important_matches(entry[2], getattr(args, "important", "all"))
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
                )
            else:
                print_entries(selected, snapshot.readiness)
        if args.count:
            print(total)
        return
    target_values = args.target_repo if isinstance(args.target_repo, list) else [args.target_repo] if args.target_repo else []
    resolved_repos = tuple(dict.fromkeys(resolve_repo_id(repo) for repo in target_values))
    readiness_target = resolved_repos[0] if len(resolved_repos) == 1 else None
    texts = EntryTexts()
    readiness = _wi_readiness.calculate_readiness(private_notes, readiness_target, texts=texts)

    selected = _wi_entries.select_entries(
        private_notes,
        status=args.status,
        target_repo=resolved_repos or None,
        entry_type=args.type,
        answered=args.answered,
        source=args.source,
        texts=texts,
        important=getattr(args, "important", "all"),
    )

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
        )
        return

    print_entries(selected, readiness)

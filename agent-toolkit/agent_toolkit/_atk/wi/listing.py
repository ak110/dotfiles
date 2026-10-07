"""agent-toolkitプラグイン配下の`atk wi`コマンド用補助モジュール。

旧`pytools/dotfiles_fb/_list.py`からの移設。PEP 723 entrypoint
`atk.py`と同一ディレクトリに配置され、`sys.path`挿入で相互import可能。
"""

import argparse
import datetime
import json
import pathlib
import shutil
import sys
from collections.abc import Mapping

from agent_toolkit._atk.environment import is_agent_environment
from agent_toolkit._atk.wi import entries as _wi_entries
from agent_toolkit._atk.wi import readiness as _wi_readiness
from agent_toolkit._atk.wi import sync as _wi_sync
from agent_toolkit._atk.wi import uwi_scan as _wi_uwi_scan
from agent_toolkit._atk.wi.constants import WI_PROCESSABLE_STATES, WI_TYPE_UWI, WI_TYPES
from agent_toolkit._atk.wi.formatters import (
    body_summary,
    display_width,
    parse_source,
    target_repo_budget,
    truncate_target_repo,
    uwi_body_summary,
)
from agent_toolkit._atk.wi.frontmatter import parse_frontmatter
from agent_toolkit._atk.wi.readiness import ReadinessResult
from agent_toolkit._atk.wi.repo import resolve_local_worktree, resolve_repo_id
from agent_toolkit._git import command as _git_command


def _state_readiness(state: str, filename: str, readiness: ReadinessResult) -> str:
    """一覧表示用の状態別着手可否を返す。"""
    if state not in WI_PROCESSABLE_STATES:
        return "complete"
    return "ready" if filename in readiness.ready else "blocked"


def _blocked_reason(readiness: ReadinessResult, filename: str) -> str | None:
    """項目の具体的なblocked理由を安定した識別子で返す。

    依存の未充足は、未終端の依存先が全て`--target-repo`の`processable`集合の内側にあるかで
    2値へ分ける。未終端の依存先を持たないblockedは、その集合の内側にある項目が終端しても解除されないため
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
            # `plan`区分は、廃止した計画ファイル付きの型で保存された終端済み項目を表示で見分けるための読取互換である。
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
    """選択済みエントリを端末幅に依存しないJSON Linesで出力する。

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
    `uwi`側は`answered`・`unanswered`で回答状況を限定する。
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
    target_values = args.target_repo if isinstance(args.target_repo, list) else [args.target_repo] if args.target_repo else []
    resolved_repos = tuple(dict.fromkeys(resolve_repo_id(repo) for repo in target_values))
    readiness_target = resolved_repos[0] if len(resolved_repos) == 1 else None
    readiness = _wi_readiness.calculate_readiness(private_notes, readiness_target)

    selected = _wi_entries.select_entries(
        private_notes,
        status=args.status,
        target_repo=resolved_repos or None,
        entry_type=args.type,
        answered=args.answered,
        source=args.source,
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

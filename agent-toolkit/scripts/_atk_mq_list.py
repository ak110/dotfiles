"""agent-toolkitプラグイン配下の`atk mq`コマンド用補助モジュール。

旧`pytools/dotfiles_fb/_list.py`からの移設。PEP 723 entrypoint
`atk.py`と同一ディレクトリに配置され、`sys.path`挿入で相互import可能。
"""

import argparse
import json
import pathlib
import shutil
import sys

from _atk_mq_common import (
    MQ_ACTIVE_STATES,
    MQ_STATES,
    MQ_TYPE_TBD,
    ReadinessResult,
    _is_tbd_answered,
    _iter_entries,
    _pull_with_recent_reuse,
    _repo_lock,
    calculate_readiness,
)
from _atk_mq_formatters import (
    _body_summary,
    _display_width,
    _parse_source,
    _source_matches,
    _target_repo_budget,
    _tbd_body_summary,
    _truncate_target_repo,
)
from _atk_mq_frontmatter import parse_frontmatter
from _atk_mq_repo import _resolve_repo_id

type QueueEntryDisplay = tuple[pathlib.Path, str, str, str, str | None]


def _resolve_states(status: str) -> tuple[str, ...]:
    """状態フィルターを走査対象へ変換する。"""
    if status == "active":
        return MQ_ACTIVE_STATES
    if status == "all":
        return MQ_STATES
    return (status,)


def _answered_matches(entry_type: str | None, text: str, answered_filter: str) -> bool:
    """回答状況フィルターとの一致を返す。"""
    if answered_filter == "all":
        return True
    if entry_type != MQ_TYPE_TBD:
        return False
    answered = _is_tbd_answered(text)
    return answered if answered_filter == "yes" else not answered


def _covers_unanswered_tbds(args: argparse.Namespace) -> bool:
    """`list`コマンドの出力が通知対象の未回答TBDを全て含むか判定する。

    次の全条件を満たす場合に`True`を返す:
    - `args.count`が`False`（整数のみ出力時は本文表示がないため対象外）
    - `args.type`が`"all"`または`"tbd"`
    - `args.status`が`"all"`または`"active"`
    - `args.answered`が`"all"`または`"no"`
    - `args.source`が`None`（source指定時は出力が部分集合になり得るため対象外）
    """
    return (
        not args.count
        and not getattr(args, "json", False)
        and args.type in ("all", "tbd")
        and args.status in ("all", "active")
        and args.answered in ("all", "no")
        and args.source is None
    )


def _blocked_reason(readiness: ReadinessResult, filename: str) -> str | None:
    """項目の具体的なblocked理由を安定した識別子で返す。"""
    reasons = (
        ("frontmatter-broken", readiness.frontmatter_broken),
        ("invalid-cooldown", readiness.invalid_cooldowns),
        ("missing-plan-file", readiness.missing_plan_file),
        ("invalid-dependency", readiness.invalid_dependencies),
        ("missing-dependency", readiness.missing_dependencies),
        ("self-dependency", readiness.self_dependencies),
        ("cyclic-dependency", readiness.cyclic_dependencies),
        ("cooldown-until", readiness.cooldown_pending),
    )
    specific = next((reason for reason, filenames in reasons if filename in filenames), None)
    if specific is not None:
        return specific
    return "dependency-unmet" if filename in readiness.blocked else None


def _print_entries(selected: list[QueueEntryDisplay], readiness: ReadinessResult) -> None:
    """選択済みエントリを`atk mq list`の1件1行形式で出力する。"""
    for header_type in ("feedback", "tbd"):
        group = [entry for entry in selected if entry[4] == header_type or (header_type == "feedback" and entry[4] is None)]
        if not group:
            continue
        print(f"# {header_type}")
        for path, target_repo, text, state, entry_type in sorted(group, key=lambda entry: entry[0].name):
            parsed = parse_frontmatter(text)
            plan_file = parsed[0].get("plan_file") if parsed is not None else None
            item_kind = "frontmatter-broken" if parsed is None else "plan" if isinstance(plan_file, str) else "normal"
            state_readiness = (
                "complete" if state not in MQ_ACTIVE_STATES else "ready" if path.name in readiness.ready else "blocked"
            )
            label = f"{state}/{item_kind}/{state_readiness}"
            answered = False
            if entry_type == MQ_TYPE_TBD:
                answered = _is_tbd_answered(text)
                label = (
                    f"{state}/answered/blocked"
                    if answered and state_readiness == "blocked"
                    else f"{state}/answered"
                    if answered
                    else f"{state}/unanswered"
                )
            repo_budget = _target_repo_budget(path.name, label)
            display_repo = _truncate_target_repo(target_repo, max_width=repo_budget)
            reason = (
                _blocked_reason(readiness, path.name)
                if state_readiness == "blocked" and (entry_type != MQ_TYPE_TBD or answered)
                else None
            )
            reason_suffix = f" blocked_reason={reason}" if reason is not None else ""
            if reason == "cooldown-until":
                cooldown_until = dict(readiness.cooldown_values)[path.name]
                reason_suffix += f" cooldown_until={cooldown_until}"
            prefix = f"{path.name}: {display_repo} [{label}]{reason_suffix} "
            available_width = shutil.get_terminal_size().columns - _display_width(prefix)
            summary = (
                _tbd_body_summary(text, available_width) if entry_type == MQ_TYPE_TBD else _body_summary(text, available_width)
            )
            print(f"{prefix}{summary}")


def _print_json_entries(selected: list[QueueEntryDisplay], readiness: ReadinessResult) -> None:
    """選択済みエントリを端末幅に依存しないJSON Linesで出力する。"""
    for path, target_repo, text, state, entry_type in sorted(selected, key=lambda entry: entry[0].name):
        actual_type = entry_type or "feedback"
        state_readiness = (
            "complete" if state not in MQ_ACTIVE_STATES else "ready" if path.name in readiness.ready else "blocked"
        )
        answered = actual_type == MQ_TYPE_TBD and _is_tbd_answered(text)
        reason = (
            _blocked_reason(readiness, path.name)
            if state_readiness == "blocked" and (actual_type != MQ_TYPE_TBD or answered)
            else None
        )
        summary = _tbd_body_summary(text, sys.maxsize) if actual_type == MQ_TYPE_TBD else _body_summary(text, sys.maxsize)
        record = {
            "filename": path.name,
            "type": actual_type,
            "target_repo": target_repo,
            "state": state,
            "ready": state in MQ_ACTIVE_STATES and path.name in readiness.ready,
            "blocked_reason": reason,
            "source": _parse_source(text),
            "summary": summary,
        }
        print(json.dumps(record, ensure_ascii=False, separators=(",", ":")))


def _cmd_list(args: argparse.Namespace, private_notes: pathlib.Path) -> None:
    """`list`サブコマンド: フィードバック/`tbd`を1件1行（ファイル名・`target_repo`・状態・要約）で出力する。

    `--type`指定で出力対象種別（feedback・tbd・all）を限定する（既定: all）。
    `--status`指定で表示範囲を限定する（既定: active）。
    `active`はフィードバック側`inbox`・`processing`と`tbd`側`answered`を出力する。
    フィードバック側は`inbox`・`processing`・`adopted`・`rejected`・`all`を解釈する。
    `tbd`側は`answered`・`unanswered`で回答状況を限定する（`inbox`・`processing`・`adopted`・`rejected`・`all`は
    `tbd`側に作用せず、`tbd` inboxの全件を返す）。
    `--source`指定時はフィードバック・`tbd`双方をfrontmatterの`source`一致（`!`接頭で否定、無指定エントリも対象に含む）へ限定する。
    `--target-repo`指定時は、正規化リモートURLへ変換した値とfrontmatterの`target_repo`が
    完全一致するエントリのみを出力する。
    出力はフィードバック・`tbd`の種別でグループ化し、各グループ内を状態によらずファイル名の昇順で整列する。
    該当エントリが1件以上ある種別だけ見出しを出力する。
    `--count`指定時は、フィルター適用後のフィードバック件数とTBD件数の合計を整数のみで出力し、
    種別見出し・エントリ行は出力しない。
    """
    if not args.skip_pull:
        with _repo_lock(private_notes):
            _pull_with_recent_reuse(private_notes, force_pull=getattr(args, "pull", False))
    filter_repo = _resolve_repo_id(args.target_repo) if args.target_repo is not None else None
    readiness = calculate_readiness(private_notes, filter_repo)

    selected: list[QueueEntryDisplay] = []
    for entry in _iter_entries(private_notes, _resolve_states(args.status), filter_repo, args.type):
        _, _, text, _, entry_type = entry
        if not _answered_matches(entry_type, text, args.answered):
            continue
        if args.source is not None and not _source_matches(_parse_source(text), args.source):
            continue
        selected.append(entry)

    if args.count:
        print(len(selected))
        return

    if args.json:
        _print_json_entries(selected, readiness)
        return

    _print_entries(selected, readiness)

"""`atk wi show`による本文の表示。"""

import argparse
import pathlib
import sys

from agent_toolkit._atk import outcome as _outcome
from agent_toolkit._atk.wi import entries as _wi_entries
from agent_toolkit._atk.wi import sync as _wi_sync
from agent_toolkit._atk.wi import uwi_scan as _wi_uwi_scan
from agent_toolkit._atk.wi.constants import WI_TYPE_UWI, WI_TYPES
from agent_toolkit._atk.wi.formatters import (
    body_summary,
    uwi_body_summary,
)
from agent_toolkit._atk.wi.repo import resolve_repo_id


def _summary_line(text: str, kind: str | None) -> str:
    """`--summary-only`で状態行の次に置く要約として、AWIはH1表題、UWIは質問本文の先頭行を省略せずに返す。

    `atk wi list --summary-only`と同じ要約を使い、AWIでは見出し記号を除いて表題だけを返す。
    状態行の後の行を`#`で始めないため、`--all`の種別見出しと区別できる。
    """
    if kind == WI_TYPE_UWI:
        return uwi_body_summary(text, sys.maxsize)
    summary = body_summary(text, sys.maxsize)
    return summary.removeprefix("# ") if summary.startswith("# ") else summary


def cmd_show(args: argparse.Namespace, private_notes: pathlib.Path) -> None:
    """showサブコマンド: `FILENAME...`指定時は指定された項目群、`--all`指定時は全件の本文を表示する。

    `FILENAME`・`--all`のいずれも未指定の場合はエラー終了する（exit 2）。
    `FILENAME`を2件以上指定した場合の区切りは`--all`と同じく各項目の後の空行1行とし、
    1件だけ指定した場合は従来どおり空行を付けない。
    `--type`指定時は出力対象種別（awi・uwi・all）を限定する（省略時はall）。
    `FILENAME...`指定時は5状態フォルダすべてを探索し、指定順に表示する。
    `--type`・`--source`と明示指定の`--target-repo`の
    値で対象を限定する。`--state`・`--answered`と省略時の`--target-repo`は迂回する
    （個別ファイル指定は明示的照会のため状態・回答有無フィルタを適用しない動作であり、
    省略時に使う`--state=active`によってadopted・rejected状態のエントリが参照不能になる事態を避けるためである。
    同じ理由で、カレントディレクトリから注入した対象リポジトリの省略時の値も、
    ファイル名で一意に指定した項目を候補から外さないよう迂回する）。
    `--all`指定時のAWI・`uwi`双方の走査対象は`--state`と連動する
    （省略時の`active`はinbox・processing・hold、`processable`はinbox・processing、
    `all`は5状態フォルダ全連結、個別状態指定はその状態のみ）。
    `--target-repo`指定時は、正規化リモートURLへ変換した値とfrontmatterの`target_repo`が
    完全一致するエントリのみを出力する。
    `--source`指定時はfrontmatterのsource一致（`!`接頭で否定、無指定エントリも対象に含む）へ限定する。
    `--answered`は`--all`分岐でuwi側の回答状況（yes・no）を限定する（省略時はall）。
    """
    if not args.filenames and not args.all:
        args.subparser.error("表示するファイル名または--allを指定してください。")
    summary_only = getattr(args, "summary_only", False)
    validated_filenames = _wi_entries.validate_named_filenames(private_notes, args.filenames)
    if not args.skip_pull:
        with _wi_sync.repo_lock(private_notes):
            _wi_sync.pull_with_recent_reuse(private_notes, force_pull=getattr(args, "pull", False))
    resolved_repos = tuple(dict.fromkeys(resolve_repo_id(repo) for repo in (args.target_repo or ())))

    if validated_filenames:
        # 省略時に使う条件は対象集合を走査する照会のためのものであり、ファイル名で一意に指定した項目へは適用しない。
        explicit_repos = () if getattr(args, "target_repo_defaulted", False) else resolved_repos
        selected_by_name, missing = _wi_entries.read_named_entries(
            private_notes, validated_filenames, target_repo=explicit_repos, entry_type=args.type, source=args.source
        )
        if missing:
            for filename in missing:
                _outcome.report_failure(
                    f"全状態フォルダに存在しない: {filename}",
                    next_action="実在するファイル名を指定し直す（`atk wi list`で候補を確認できる）",
                )
            sys.exit(2)
        for path, target_repo, text, state, kind in selected_by_name:
            answered = _wi_uwi_scan.is_uwi_answered(text)
            label = f" [{state}]"
            if kind == WI_TYPE_UWI:
                label = f" [{state}/{'answered' if answered else 'unanswered'}]"
            print(f"## target_repo: {target_repo}")
            print(f"### {path.name}{label}")
            print(_summary_line(text, kind) if summary_only else text)
            if len(selected_by_name) > 1:
                print()
        return

    selected = _wi_entries.select_entries(
        private_notes,
        status=args.status,
        target_repo=resolved_repos or None,
        entry_type=args.type,
        answered=args.answered,
        source=args.source,
    )
    for header_type in WI_TYPES:
        entries: dict[str, list[tuple[str, str, str]]] = {}
        for path, target_repo, text, state, entry_type in selected:
            if entry_type != header_type:
                continue
            answered = _wi_uwi_scan.is_uwi_answered(text)
            entries.setdefault(target_repo, []).append((path.name, text, state))
        if entries:
            print(f"# {header_type}")
            for repo, items in entries.items():
                print(f"## target_repo: {repo}")
                for name, text, state in items:
                    label = f" [{state}]"
                    if header_type == WI_TYPE_UWI:
                        label = f" [{state}/{'answered' if _wi_uwi_scan.is_uwi_answered(text) else 'unanswered'}]"
                    print(f"### {name}{label}")
                    print(_summary_line(text, header_type) if summary_only else text)
                    print()

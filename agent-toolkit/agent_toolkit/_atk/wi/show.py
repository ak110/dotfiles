"""agent-toolkitプラグイン配下の`atk wi`コマンド用補助モジュール。

旧`pytools/dotfiles_fb/_show.py`からの移設。PEP 723 entrypoint
`atk.py`と同一ディレクトリに配置され、`sys.path`挿入で相互import可能。
"""

import argparse
import pathlib
import sys

from agent_toolkit._atk import outcome as _outcome
from agent_toolkit._atk.wi.common import (
    WI_PROCESSABLE_STATES,
    WI_STATES,
    WI_TYPE_UWI,
    WI_TYPES,
    _canonical_repo,
    _dedup_positional_filenames,
    _is_uwi_answered,
    _pull_with_recent_reuse,
    _repo_lock,
    _require_type,
    _validate_filename,
)
from agent_toolkit._atk.wi.formatters import _parse_source, _parse_target_repo, _source_matches
from agent_toolkit._atk.wi.listing import _resolve_states, _select_entries
from agent_toolkit._atk.wi.repo import _resolve_repo_id


def _covers_unanswered_uwis(args: argparse.Namespace) -> bool:
    """`show --all`コマンドの出力が通知対象の未回答UWIを全て含むか判定する。

    次の全条件を満たす場合に`True`を返す:
    - `args.filenames`が空かつ`args.all`が`True`（ファイル指定は全集合対象外）
    - `args.type`が`"all"`または`"uwi"`
    - `args.status`（`--state`の値）が`"all"`または`"active"`
    - `args.answered`が`"all"`または`"no"`
    - `args.source`が`None`
    """
    return (
        not args.filenames
        and args.all
        and ("all" in args.type or WI_TYPE_UWI in args.type)
        and set(WI_PROCESSABLE_STATES).issubset(_resolve_states(args.status))
        and ("all" in args.answered or "no" in args.answered)
        and args.source is None
    )


def _state_prefixed_filename_hint(filename: str) -> str | None:
    """`<状態名>/<ファイル名>`形式の入力に対する案内文を返す。該当しない場合は`None`を返す。

    `show`は5状態フォルダすべてを探索するため、状態名を含む入力は受理しない。
    共通のファイル名検証は`不正なファイル名`としか示さず正しい入力形式を判断できないため、
    この形式に限って再実行方法を案内する。共通検証自体は緩和しない。
    """
    parts = filename.replace("\\", "/").split("/")
    if len(parts) != 2 or parts[0] not in WI_STATES:
        return None
    remainder = parts[1]
    if not remainder or remainder in (".", ".."):
        return None
    return f"状態名を除いたファイル名を指定する: {remainder}（showは全状態フォルダを探索する）"


def _cmd_show(args: argparse.Namespace, private_notes: pathlib.Path) -> None:
    """showサブコマンド: `FILENAME...`指定時は当該項目群、`--all`指定時は全件の本文を表示する。

    `FILENAME`・`--all`のいずれも未指定の場合はエラー終了する（exit 2）。
    `FILENAME`を2件以上指定した場合の区切りは`--all`と同じく各項目の後の空行1行とし、
    1件だけ指定した場合は従来どおり空行を付けない。
    `--type`指定時は出力対象種別（awi・uwi・all）を限定する（既定: all）。
    `FILENAME...`指定時は5状態フォルダすべてを探索し、指定順に表示する。
    `--type`・`--target-repo`・`--source`の
    値で対象を限定する。`--state`・`--answered`は迂回する（個別ファイル指定は明示的照会のため
    状態・回答有無フィルタを迂回する既定挙動であり、既定の`--state=active`によって
    adopted・rejected状態のエントリが参照不能になる事態を避けるためである）。
    `--all`指定時のAWI・`uwi`双方の走査対象は`--state`と連動する
    （既定`active`はinbox・processing・hold、`processable`はinbox・processing、
    `all`は5状態フォルダ全連結、個別状態指定は当該状態のみ）。
    `--target-repo`指定時は、正規化リモートURLへ変換した値とfrontmatterの`target_repo`が
    完全一致するエントリのみを出力する。
    `--source`指定時はfrontmatterのsource一致（`!`接頭で否定、無指定エントリも対象に含む）へ限定する。
    `--answered`は`--all`分岐でuwi側の回答状況（yes・no）を限定する（既定: all）。
    """
    if not args.filenames and not args.all:
        args.subparser.error("表示するファイル名または--allを指定してください。")
    for filename in args.filenames:
        hint = _state_prefixed_filename_hint(filename)
        if hint is not None:
            _outcome.report_failure(hint)
            sys.exit(2)
    filenames = _dedup_positional_filenames(args.filenames, "show")
    validated_filenames = [
        (filename, _validate_filename(filename, private_notes / WI_STATES[0]).name) for filename in filenames
    ]
    if not args.skip_pull:
        with _repo_lock(private_notes):
            _pull_with_recent_reuse(private_notes, force_pull=getattr(args, "pull", False))
    resolved_repos = tuple(dict.fromkeys(_resolve_repo_id(repo) for repo in (args.target_repo or ())))

    if validated_filenames:
        resolver_cache: dict[str, str | None] = {}
        selected_by_name: list[tuple[pathlib.Path, str, str, str, str | None]] = []
        missing: list[str] = []
        for requested_filename, normalized_filename in validated_filenames:
            selected_entry: tuple[pathlib.Path, str, str, str, str | None] | None = None
            for state in WI_STATES:
                path = private_notes / state / normalized_filename
                if not path.exists():
                    continue
                text = path.read_text(encoding="utf-8")
                kind = _require_type(path, text)
                if "all" not in args.type and kind not in args.type:
                    continue
                target_repo = _parse_target_repo(text)
                if resolved_repos and _canonical_repo(target_repo, resolver_cache) not in resolved_repos:
                    continue
                if args.source is not None and not any(_source_matches(_parse_source(text), source) for source in args.source):
                    continue
                selected_entry = (path, target_repo, text, state, kind)
                break
            if selected_entry is None:
                missing.append(requested_filename)
            else:
                selected_by_name.append(selected_entry)
        if missing:
            for filename in missing:
                _outcome.report_failure(f"全状態フォルダに存在しない: {filename}。実在するファイル名を指定し直す")
            sys.exit(2)
        for path, target_repo, text, state, kind in selected_by_name:
            answered = _is_uwi_answered(text)
            label = f" [{state}]"
            if kind == WI_TYPE_UWI:
                label = f" [{state}/{'answered' if answered else 'unanswered'}]"
            print(f"## target_repo: {target_repo}")
            print(f"### {path.name}{label}")
            print(text)
            if len(selected_by_name) > 1:
                print()
        return

    selected = _select_entries(
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
            answered = _is_uwi_answered(text)
            entries.setdefault(target_repo, []).append((path.name, text, state))
        if entries:
            print(f"# {header_type}")
            for repo, items in entries.items():
                print(f"## target_repo: {repo}")
                for name, text, state in items:
                    label = f" [{state}]"
                    if header_type == WI_TYPE_UWI:
                        label = f" [{state}/{'answered' if _is_uwi_answered(text) else 'unanswered'}]"
                    print(f"### {name}{label}")
                    print(text)
                    print()

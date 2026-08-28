"""agent-toolkitプラグイン配下の`atk mq`コマンド用補助モジュール。

旧`pytools/dotfiles_fb/_show.py`からの移設。PEP 723 entrypoint
`atk.py`と同一ディレクトリに配置され、`sys.path`挿入で相互import可能。
"""

import argparse
import pathlib
import sys

from _atk_mq_common import (
    MQ_FEEDBACK_ACTIVE_STATES,
    MQ_PROCESSABLE_STATES,
    MQ_STATE_PLANNING,
    MQ_STATES,
    MQ_TYPE_TBD,
    _canonical_repo,
    _dedup_positional_filenames,
    _is_tbd_answered,
    _iter_entries,
    _pull_with_recent_reuse,
    _repo_lock,
    _require_type,
    _validate_filename,
)
from _atk_mq_formatters import _parse_source, _parse_target_repo, _source_matches
from _atk_mq_repo import _resolve_repo_id


def _covers_unanswered_tbds(args: argparse.Namespace) -> bool:
    """`show --all`コマンドの出力が通知対象の未回答TBDを全て含むか判定する。

    次の全条件を満たす場合に`True`を返す:
    - `args.filenames`が空かつ`args.all`が`True`（ファイル指定は全集合対象外）
    - `args.type`が`"all"`または`"tbd"`
    - `args.status`が`"all"`または`"active"`
    - `args.answered`が`"all"`または`"no"`
    - `args.source`が`None`
    """
    return (
        not args.filenames
        and args.all
        and args.type in ("all", "tbd")
        and args.status in ("all", "active")
        and args.answered in ("all", "no")
        and args.source is None
    )


def _state_prefixed_filename_hint(filename: str) -> str | None:
    """`<状態名>/<ファイル名>`形式の入力に対する案内文を返す。該当しない場合は`None`を返す。

    `show`は5状態フォルダすべてを探索するため、状態名を含む入力は受理しない。
    共通のファイル名検証は`不正なファイル名`としか示さず正しい入力形式を判断できないため、
    この形式に限って再実行方法を案内する。共通検証自体は緩和しない。
    """
    parts = filename.replace("\\", "/").split("/")
    if len(parts) != 2 or parts[0] not in MQ_STATES:
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
    `--type`指定時は出力対象種別（feedback・tbd・all）を限定する（既定: all）。
    `FILENAME...`指定時は5状態フォルダすべてを探索し、指定順に表示する。
    `--type`・`--target-repo`・`--source`の
    値で対象を限定する。`--status`・`--answered`は迂回する（個別ファイル指定は明示的照会のため
    状態・回答有無フィルタを迂回する既定挙動であり、既定の`--status=active`によって
    adopted・rejected状態のエントリが参照不能になる事態を避けるためである）。
    `--all`指定時のフィードバック・`tbd`双方の走査対象は`--status`と連動する
    （既定`active`はフィードバックがinbox・planning・processing、TBDがinbox・processing、
    `all`は5状態フォルダ全連結、個別状態指定は当該状態のみ）。
    `--target-repo`指定時は、正規化リモートURLへ変換した値とfrontmatterの`target_repo`が
    完全一致するエントリのみを出力する。
    `--source`指定時はfrontmatterのsource一致（`!`接頭で否定、無指定エントリも対象に含む）へ限定する。
    `--answered`は`--all`分岐でtbd側の回答状況（yes・no）を限定する（既定: all）。
    """
    if not args.filenames and not args.all:
        args.subparser.error("表示するファイル名または--allを指定してください。")
    for filename in args.filenames:
        hint = _state_prefixed_filename_hint(filename)
        if hint is not None:
            print(hint, file=sys.stderr)
            sys.exit(2)
    filenames = _dedup_positional_filenames(args.filenames, "show")
    validated_filenames = [
        (filename, _validate_filename(filename, private_notes / MQ_STATES[0]).name) for filename in filenames
    ]
    if not args.skip_pull:
        with _repo_lock(private_notes):
            _pull_with_recent_reuse(private_notes, force_pull=getattr(args, "pull", False))
    filter_repo: str | None = None
    if args.target_repo is not None:
        filter_repo = _resolve_repo_id(args.target_repo)

    if validated_filenames:
        resolver_cache: dict[str, str | None] = {}
        selected_by_name: list[tuple[pathlib.Path, str, str, str, str | None]] = []
        missing: list[str] = []
        for requested_filename, normalized_filename in validated_filenames:
            selected_entry: tuple[pathlib.Path, str, str, str, str | None] | None = None
            for state in MQ_STATES:
                path = private_notes / state / normalized_filename
                if not path.exists():
                    continue
                text = path.read_text(encoding="utf-8")
                kind = _require_type(path, text)
                if args.type not in ("all", kind):
                    continue
                target_repo = _parse_target_repo(text)
                if filter_repo is not None and _canonical_repo(target_repo, resolver_cache) != filter_repo:
                    continue
                if args.source is not None and not _source_matches(_parse_source(text), args.source):
                    continue
                selected_entry = (path, target_repo, text, state, kind)
                break
            if selected_entry is None:
                missing.append(requested_filename)
            else:
                selected_by_name.append(selected_entry)
        if missing:
            for filename in missing:
                print(f"全状態フォルダに存在しません: {filename}", file=sys.stderr)
            sys.exit(2)
        for path, target_repo, text, state, kind in selected_by_name:
            answered = _is_tbd_answered(text)
            label = f" [{state}]"
            if kind == MQ_TYPE_TBD:
                label = f" [{state}/{'answered' if answered else 'unanswered'}]"
            print(f"## target_repo: {target_repo}")
            print(f"### {path.name}{label}")
            print(text)
            if len(selected_by_name) > 1:
                print()
        return

    states = (
        MQ_FEEDBACK_ACTIVE_STATES
        if args.status == "active"
        else MQ_PROCESSABLE_STATES
        if args.status == "processable"
        else MQ_STATES
        if args.status == "all"
        else (args.status,)
    )
    selected = list(_iter_entries(private_notes, states, filter_repo, args.type))
    for header_type in ("feedback", "tbd"):
        entries: dict[str, list[tuple[str, str, str]]] = {}
        for path, target_repo, text, state, entry_type in selected:
            if args.status in {"active", "processable"} and state == MQ_STATE_PLANNING and entry_type == MQ_TYPE_TBD:
                continue
            if entry_type != header_type:
                continue
            answered = _is_tbd_answered(text)
            if args.answered == "yes" and (entry_type != MQ_TYPE_TBD or not answered):
                continue
            if args.answered == "no" and (entry_type != MQ_TYPE_TBD or answered):
                continue
            if args.source is not None and not _source_matches(_parse_source(text), args.source):
                continue
            entries.setdefault(target_repo, []).append((path.name, text, state))
        if entries:
            print(f"# {header_type}")
            for repo, items in entries.items():
                print(f"## target_repo: {repo}")
                for name, text, state in items:
                    label = f" [{state}]"
                    if header_type == MQ_TYPE_TBD:
                        label = f" [{state}/{'answered' if _is_tbd_answered(text) else 'unanswered'}]"
                    print(f"### {name}{label}")
                    print(text)
                    print()

"""agent-toolkitプラグイン配下の`atk mq`コマンド用補助モジュール。

旧`pytools/dotfiles_fb/_show.py`からの移設。PEP 723 entrypoint
`atk.py`と同一ディレクトリに配置され、`sys.path`挿入で相互import可能。
"""

import argparse
import pathlib
import sys

from _atk_mq_common import (
    MQ_ACTIVE_STATES,
    MQ_STATES,
    MQ_TYPE_TBD,
    _is_tbd_answered,
    _iter_entries,
    _pull,
    _repo_lock,
    _require_type,
    _validate_filename,
)
from _atk_mq_formatters import _parse_source, _parse_target_repo, _source_matches
from _atk_mq_repo import _resolve_repo_id


def _cmd_show(args: argparse.Namespace, private_notes: pathlib.Path) -> None:
    """showサブコマンド: `FILENAME`指定時は当該1件、`--all`指定時は全件の本文を表示する。

    `FILENAME`・`--all`のいずれも未指定の場合はエラー終了する（exit 2）。
    `--type`指定時は出力対象種別（feedback・tbd・all）を限定する（既定: all）。
    `FILENAME`指定時は4状態フォルダすべてを探索し、`--type`・`--target-repo`・`--source`の
    値で対象を限定する。`--status`・`--answered`は迂回する（個別ファイル指定は明示的照会のため
    状態・回答有無フィルタを迂回する既定挙動であり、既定の`--status=active`によって
    adopted・rejected状態のエントリが参照不能になる事態を避けるためである）。
    `--all`指定時のfeedback・tbd双方の走査対象は`--status`と連動する
    （既定`active`はinbox・processing、`all`は4状態フォルダ全連結、個別状態指定は当該状態のみ）。
    `--target-repo`指定時は、正規化リモートURLへ変換した値とfrontmatterの`target_repo`が
    完全一致するエントリのみを出力する。
    `--source`指定時はfrontmatterのsource一致（`!`接頭で否定、無指定エントリも対象に含む）へ限定する。
    `--answered`は`--all`分岐でtbd側の回答状況（yes・no）を限定する（既定: all）。
    """
    if args.filename is None and not args.all:
        args.subparser.error("表示するファイル名または--allを指定してください。")
    if not args.skip_pull:
        with _repo_lock(private_notes):
            _pull(private_notes)
    filter_repo: str | None = None
    if args.target_repo is not None:
        filter_repo = _resolve_repo_id(args.target_repo)

    if args.filename is not None:
        for state in MQ_STATES:
            base_dir = private_notes / state
            path = _validate_filename(args.filename, base_dir)
            if not path.exists():
                continue
            text = path.read_text(encoding="utf-8")
            kind = _require_type(path, text)
            if args.type not in ("all", kind):
                continue
            target_repo = _parse_target_repo(text)
            if filter_repo is not None and target_repo != filter_repo:
                continue
            if args.source is not None and not _source_matches(_parse_source(text), args.source):
                continue
            answered = _is_tbd_answered(text)
            label = f" [{state}]"
            if kind == MQ_TYPE_TBD:
                label = f" [{state}/{'answered' if answered else 'unanswered'}]"
            print(f"## target_repo: {target_repo}")
            print(f"### {path.name}{label}")
            print(text)
            return
        print(f"全状態フォルダに存在しません: {args.filename}", file=sys.stderr)
        sys.exit(2)

    states = MQ_ACTIVE_STATES if args.status == "active" else MQ_STATES if args.status == "all" else (args.status,)
    selected = list(_iter_entries(private_notes, states, filter_repo, args.type))
    for header_type in ("feedback", "tbd"):
        entries: dict[str, list[tuple[str, str, str]]] = {}
        for path, target_repo, text, state, entry_type in selected:
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

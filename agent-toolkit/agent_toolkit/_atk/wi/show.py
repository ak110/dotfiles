"""`atk wi show`による本文の表示。"""

import argparse
import dataclasses
import pathlib
import sys

from agent_toolkit._atk import outcome as _outcome
from agent_toolkit._atk.wi import entries as _wi_entries
from agent_toolkit._atk.wi import frontmatter as _frontmatter
from agent_toolkit._atk.wi import sections as _wi_sections
from agent_toolkit._atk.wi import sync as _wi_sync
from agent_toolkit._atk.wi import user_comment as _user_comment
from agent_toolkit._atk.wi import uwi as _uwi
from agent_toolkit._atk.wi import uwi_scan as _wi_uwi_scan
from agent_toolkit._atk.wi.constants import WI_TYPE_UWI, WI_TYPES
from agent_toolkit._atk.wi.formatters import (
    body_summary,
    uwi_body_summary,
)
from agent_toolkit._atk.wi.repo import resolve_repo_id
from agent_toolkit._common.markdown_headings import top_level_atx_headings


@dataclasses.dataclass
class _ShowOutput:
    """表示した行数と、同じ起動の保存後に示す記入欄の情報を保持する。"""

    user_fields: list[str] | None
    line: int = 1

    def write(self, text: str = "") -> None:
        """printと同じ改行を加え、次に書く行の位置を進める。"""
        print(text)
        self.line += text.count("\n") + 1

    def body(self, name: str, text: str, kind: str | None, args: argparse.Namespace) -> None:
        """本文と記入欄の開始行を、表示する元の項目から同時に取得する。"""
        start = self.line
        self.write(_display_body(text, kind, args))
        if self.user_fields is None:
            return
        heading = _uwi.ANSWER_HEADING if kind == WI_TYPE_UWI else "## ユーザーコメント"
        position: int | None = None
        if kind == WI_TYPE_UWI or _user_comment.has_reserved_heading(text):
            normalized = _frontmatter.normalize_newlines(text)
            parsed = _frontmatter.parse_frontmatter(normalized)
            body = parsed[1] if parsed is not None else normalized
            prefix_lines = normalized.count("\n") - body.count("\n")
            for token, content in top_level_atx_headings(body, 2):
                if f"## {content}" == heading:
                    assert token.map is not None
                    position = prefix_lines + token.map[0]
                    break
        location = "なし" if position is None else f"あり 開始行: {start + position}"
        self.user_fields.append(f"記入欄: {name} {heading} {location}")


def _summary_line(text: str, kind: str | None) -> str:
    """`--summary-only`で状態行の次に置く要約として、AWIはH1表題、UWIは質問本文の先頭行を省略せずに返す。

    `atk wi list --summary-only`と同じ要約を使い、AWIでは見出し記号を除いて表題だけを返す。
    状態行の後の行を`#`で始めないため、`--all`の種別見出しと区別できる。
    """
    if kind == WI_TYPE_UWI:
        return uwi_body_summary(text, sys.maxsize)
    summary = body_summary(text, sys.maxsize)
    return summary.removeprefix("# ") if summary.startswith("# ") else summary


def _display_body(text: str, kind: str | None, args: argparse.Namespace) -> str:
    """全文・要約・指定節のいずれかを表示用に取り出す。"""
    if getattr(args, "summary_only", False):
        return _summary_line(text, kind)
    names = getattr(args, "section", None)
    if names is None:
        return text
    return "\n\n".join(f"#### {name}\n{body}" for name, body in _wi_sections.h2_section_items(text) if name in names)


def cmd_show(args: argparse.Namespace, private_notes: pathlib.Path) -> None:
    """showサブコマンド: `FILENAME...`指定時は指定された項目群、`--all`指定時は全件の本文を表示する。

    `FILENAME`・`--all`のいずれも未指定の場合はエラー終了する（exit 2）。
    `FILENAME`を2件以上指定した場合の区切りは`--all`と同じく各項目の後の空行1行とし、
    1件だけ指定した場合は従来どおり空行を付けない。
    `--type`指定時は出力対象種別（awi・uwi・all）を限定する（省略時はall）。
    `FILENAME...`指定時は年月配置を探索し、指定順に表示する。
    `--type`・`--source`と明示指定の`--target-repo`の
    値で対象を限定する。`--state`・`--answered`と省略時の`--target-repo`は迂回する
    （個別ファイル指定は明示的照会のため状態・回答有無フィルタを適用しない動作であり、
    省略時に使う`--state=active`によってadopted・rejected状態のエントリが参照不能になる事態を避けるためである。
    同じ理由で、カレントディレクトリから注入した対象リポジトリの省略時の値も、
    ファイル名で一意に指定した項目を候補から外さないよう迂回する）。
    `--all`指定時のAWI・`uwi`双方の走査対象は`--state`と連動する
    （省略時の`active`はinbox・processing・hold、`processable`はinbox・processing、
    `all`は全保存状態、個別状態指定はその状態のみ）。
    `--target-repo`指定時は、正規化リモートURLへ変換した値とfrontmatterの`target_repo`が
    完全一致するエントリのみを出力する。
    `--source`指定時はfrontmatterのsource一致（`!`接頭で否定、無指定エントリも対象に含む）へ限定する。
    `--answered`は`--all`分岐でuwi側の回答状況（yes・no）を限定する（省略時はall）。
    """
    output = _ShowOutput(getattr(args, "_saved_show_fields", None))
    if not args.filenames and not args.all:
        args.subparser.error("表示するファイル名または--allを指定してください。")
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
                    f"年月配置に存在しない: {filename}",
                    next_action="実在するファイル名を指定し直す（`atk wi list`で候補を確認できる）",
                )
            sys.exit(2)
        for path, target_repo, text, state, kind in selected_by_name:
            answered = _wi_uwi_scan.is_uwi_answered(text)
            label = f" [{state}]"
            if kind == WI_TYPE_UWI:
                label = f" [{state}/{'answered' if answered else 'unanswered'}]"
            output.write(f"## target_repo: {target_repo}")
            output.write(f"### {path.name}{label}")
            output.body(path.name, text, kind, args)
            if len(selected_by_name) > 1:
                output.write()
        return

    selected = _wi_entries.select_entries(
        private_notes,
        status=args.status,
        target_repo=resolved_repos or None,
        entry_type=args.type,
        answered=args.answered,
        source=args.source,
        important=getattr(args, "important", "all"),
    )
    for header_type in WI_TYPES:
        entries: dict[str, list[tuple[str, str, str]]] = {}
        for path, target_repo, text, state, entry_type in selected:
            if entry_type != header_type:
                continue
            answered = _wi_uwi_scan.is_uwi_answered(text)
            entries.setdefault(target_repo, []).append((path.name, text, state))
        if entries:
            output.write(f"# {header_type}")
            for repo, items in entries.items():
                output.write(f"## target_repo: {repo}")
                for name, text, state in items:
                    label = f" [{state}]"
                    if header_type == WI_TYPE_UWI:
                        label = f" [{state}/{'answered' if _wi_uwi_scan.is_uwi_answered(text) else 'unanswered'}]"
                    output.write(f"### {name}{label}")
                    output.body(name, text, header_type, args)
                    output.write()

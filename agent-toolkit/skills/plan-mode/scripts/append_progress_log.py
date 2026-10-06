"""計画ファイルの進捗ログへ実行時刻を含む1行を追記する。

保存済み計画の領域（private-notesの`plans`配下）の計画は変更せずに失敗する。
保存済み計画へ追記する場合は、`atk plans checkout`で`~/.claude/plans`へ取得してから追記し、`atk plans commit`で保存する。
`--commit`と`--rewrite-map`の対応は、計画（`--handoff`では引き継ぎ記録）と同じディレクトリで同じstemの
`<stem>.wi-commits.jsonl`へ短縮OIDで記録し、本文には進捗の行だけを追記する。
"""

from __future__ import annotations

import argparse
import datetime
import json
import pathlib
import sys
from collections.abc import Callable

try:
    from agent_toolkit._common import next_action as _next_action
    from agent_toolkit._common.atomic_file import atomic_write
    from agent_toolkit._common.markdown_headings import top_level_atx_headings
    from agent_toolkit._plan import commit_mapping
    from agent_toolkit._plan import locations as _plan_locations
    from agent_toolkit._plan import structure as _plan_format
except ImportError as _import_error:
    _SELF = pathlib.Path(__file__).resolve()
    print(
        f"agent_toolkitパッケージを解決できません: {_import_error}\n"
        # パッケージを読めない場合に実行されるため共通の出力関数を使えず、同じ標識を直接書く。
        "次の操作: `atk run-script plan-progress -- <引数>`で起動する",
        file=sys.stderr,
    )
    sys.exit(2)

Clock = Callable[[], datetime.datetime]


_CHECK_STRUCTURE = (
    "`atk run-script plan-check -- <計画ファイルの絶対パス>`で計画の構造が基準を満たすか確かめ、"
    "指摘どおりに直してから再実行する"
)


class ProgressLogError(_next_action.ActionableError):
    """進捗ログを安全に更新できない場合のエラー。理由と次の操作を持つ。"""


def _local_now() -> datetime.datetime:
    """実行ホストのタイムゾーンを持つ現在時刻を返す。"""
    return datetime.datetime.now().astimezone()


def _escape_cell(value: str) -> str:
    """GFM表の1セルとして復元できる文字列へ変換する。"""
    normalized = value.replace("\r\n", "\n").replace("\r", "\n")
    return normalized.replace("\\", "\\\\").replace("|", "\\|").replace("\n", "<br>")


def _heading_names() -> frozenset[str]:
    """現行形式と読み取り互換形式の進捗ログ見出しを返す。"""
    return frozenset(
        {
            _plan_format.PLAN_H2_PROGRESS,
            _plan_format.PLAN_H2_CURRENT_PROGRESS,
            _plan_format.PLAN_H2_LEGACY_PROGRESS,
        }
    )


def append_progress_log(
    plan_file: pathlib.Path | str,
    completed_step: str,
    result: str,
    *,
    clock: Clock = _local_now,
    writer: Callable[[pathlib.Path, str], None] = atomic_write,
) -> None:
    """計画の固定3列表へ現在時刻を含む1行だけを原子的に追加する。"""
    path = pathlib.Path(plan_file)
    _plan_locations.reject_saved_plans_root_write(path)
    original = path.read_bytes()
    try:
        content = original.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ProgressLogError(
            "計画ファイルをUTF-8として読めません", next_action="計画ファイルをUTF-8で保存し直してから再実行する"
        ) from error

    lines = content.splitlines(keepends=True)
    headings = top_level_atx_headings(content, 2)
    matches = [index for index, (_, title) in enumerate(headings) if title in _heading_names()]
    if len(matches) != 1:
        raise ProgressLogError(f"進捗ログ見出しは1件必要です（実際={len(matches)}件）", next_action=_CHECK_STRUCTURE)

    try:
        _plan_format.progress_log_rows(content)
    except _next_action.ActionableError as error:
        raise ProgressLogError(error.reason, next_action=error.next_action) from error
    except ValueError as error:
        raise ProgressLogError(str(error), next_action=_CHECK_STRUCTURE) from error

    position = matches[0]
    token = headings[position][0]
    assert token.map is not None
    section_start = token.map[1]
    following = headings[position + 1][0] if position + 1 < len(headings) else None
    section_end = following.map[0] if following is not None and following.map is not None else len(lines)
    header_text = "| " + " | ".join(_plan_format.PLAN_PROGRESS_TABLE_HEADER) + " |"
    table_headers = [index for index in range(section_start, section_end) if lines[index].rstrip("\r\n").strip() == header_text]
    if len(table_headers) != 1:
        raise ProgressLogError(f"進捗ログの固定表は1件必要です（実際={len(table_headers)}件）", next_action=_CHECK_STRUCTURE)

    header_index = table_headers[0]
    if header_index + 1 >= section_end or not lines[header_index + 1].rstrip("\r\n").strip().startswith("|"):
        raise ProgressLogError("進捗ログの固定表に区切り行がありません", next_action=_CHECK_STRUCTURE)
    insertion = header_index + 2
    while insertion < section_end and lines[insertion].rstrip("\r\n").strip().startswith("|"):
        insertion += 1

    now = clock()
    if now.tzinfo is None:
        raise ProgressLogError(
            "進捗ログの時計にはタイムゾーンが必要です",
            next_action="`clock`へタイムゾーン付きの時刻を返す関数を渡す（CLIからの呼び出しでは発生しない）",
        )
    row = f"| {now:%Y-%m-%d %H:%M} | {_escape_cell(completed_step)} | {_escape_cell(result)} |"
    newline = "\r\n" if "\r\n" in content else "\n"
    if insertion == len(lines):
        if lines and not lines[-1].endswith(("\n", "\r")):
            lines[-1] += newline
            row_line = row
        else:
            row_line = row + newline
    else:
        row_line = row + newline
    lines.insert(insertion, row_line)
    writer(path, "".join(lines))


def _record_mapping(args: argparse.Namespace, parser: argparse.ArgumentParser) -> bool:
    """対応の記録・取得を処理し、取得だけで終了する場合は真を返す。"""
    if not (
        args.commit
        or args.previous_head
        or args.rewrite_map
        or args.get_commits
        or args.handoff
        or args.awi
        or args.allowed_awi
    ):
        return False
    if args.commit and args.previous_head is None:
        parser.error("--commitには作成前のHEADを--previous-headで渡す必要があります")
    if args.previous_head is not None and not args.commit:
        parser.error("--previous-headは--commitと組で指定します")
    if args.worktree is None or not args.worktree.is_absolute():
        parser.error("commit対応には絶対パスの--worktreeが必要です")
    read_path = args.plan_file
    if args.get_commits and not args.handoff and not read_path.is_file():
        working_root = _plan_locations.working_plans_root().resolve()
        if read_path.resolve().parent != working_root:
            raise ProgressLogError(
                f"計画ファイルが存在しません: {read_path}",
                next_action="実在する計画ファイルの絶対パスを指定する",
            )
        candidates = sorted(path for path in _plan_locations.new_plans_root().rglob(read_path.name) if path.is_file())
        if len(candidates) != 1:
            raise ProgressLogError(
                f"保存済み計画を一意に特定できません（同名={len(candidates)}件）: {read_path.name}",
                next_action="実在する保存済み計画ファイルの絶対パスを指定する",
            )
        read_path = candidates[0]
    content = read_path.read_text(encoding="utf-8")
    if args.handoff:
        allowed = commit_mapping.validate_wis(args.allowed_awi or [])
    else:
        metadata, errors = _plan_format.parse_plan_metadata(content)
        if metadata is None or errors:
            raise ProgressLogError("計画の関連WIを確定できません", next_action=_CHECK_STRUCTURE)
        allowed = {wi for wi, _summary in metadata.related_wi}
    events = commit_mapping.read_events(read_path, content)
    if args.get_commits:
        result = commit_mapping.get_commits(args.worktree, events, args.awi or [], allowed)
        for wi, commits in result.items():
            print(json.dumps({"awi": wi, "commits": commits}, ensure_ascii=False))
        return True
    if args.commit:
        assert isinstance(args.previous_head, str)
        event = commit_mapping.commit_event(args.worktree, args.commit, args.previous_head, args.awi or [], allowed)
    elif args.rewrite_map:
        mapping = commit_mapping.read_mapping(args.worktree, events, allowed)
        event = commit_mapping.rewrite_event(args.worktree, args.rewrite_map, mapping)
    else:
        parser.error("--handoffには--commit、--rewrite-mapまたは--get-commitsが必要です")
        return False
    # 進捗の行を先に追記し、構造の不正で失敗した場合は対応記録ファイルも変えない。
    if args.handoff:
        _plan_locations.reject_saved_plans_root_write(args.plan_file)
        separator = "\n" if content.endswith("\n") else "\n\n"
        atomic_write(args.plan_file, content + separator + args.completed_step + ": " + args.result + "\n")
    else:
        append_progress_log(args.plan_file, args.completed_step, args.result)
    commit_mapping.append_event(args.plan_file, event)
    return True


def main(argv: list[str] | None = None, *, description: str | None = None) -> int:
    """CLIから進捗ログの追記を開始する。`description`は別名の公開コマンドのヘルプ説明。"""
    parser = argparse.ArgumentParser(description=description or __doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("plan_file", type=pathlib.Path, metavar="PATH", help="更新する計画ファイルのパス")
    parser.add_argument("--completed-step", help="完了した工程（記録時は必須）")
    parser.add_argument("--result", help="結果・特記事項（記録時は必須）")
    operation = parser.add_mutually_exclusive_group()
    operation.add_argument("--commit", help="対応を記録する実装commit。短縮OIDで記録する")
    parser.add_argument("--previous-head", help="実装commitの作成直前に取得したHEAD。短縮OIDか完全OID。--commitでは必須")
    operation.add_argument(
        "--rewrite-map",
        type=pathlib.Path,
        metavar="PATH",
        help="検収済みの旧OIDから新OIDへの対応をJSONオブジェクトで保存したファイルの絶対パス。OIDは短縮OIDか完全OID（JSON文字列そのものは受け取らない）",
    )
    operation.add_argument(
        "--get-commits", action="store_true", help="対象AWIの現在のcommit対応を短縮OIDのJSON Linesで取得する"
    )
    parser.add_argument("--awi", action="append", help="対応する、または取得するAWIファイル名。反復指定")
    parser.add_argument("--worktree", type=pathlib.Path, metavar="DIR", help="実装commitを確認する対象worktreeの絶対パス")
    parser.add_argument("--handoff", action="store_true", help="計画なしの引き継ぎ記録について同じ対応を記録・取得する")
    parser.add_argument("--allowed-awi", action="append", help="引き継ぎ記録の対象AWI全件。--handoffでは反復指定が必須")
    args = parser.parse_args(argv)
    if not args.get_commits and (args.completed_step is None or args.result is None):
        parser.error("記録には--completed-stepと--resultが必要です")
    try:
        if _record_mapping(args, parser):
            return 0
        append_progress_log(args.plan_file, args.completed_step, args.result)
    except _next_action.ActionableError as error:
        # 保存済み計画の直接更新（`_plan.locations`）もこの型で次の操作を持って届く。
        _next_action.report(f"進捗ログを更新できません: {error.reason}", next_action=error.next_action)
        return 1
    except (OSError, ValueError) as error:
        _next_action.report(
            f"進捗ログを更新できません: {error}",
            next_action="計画ファイルのパスと読み書きの権限を確かめ、同じ引数で再実行する",
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

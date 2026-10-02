"""計画ファイルの進捗ログへ実行時刻を含む1行を追記する。

保存済み計画の領域（private-notesの`plans`配下）の計画は変更せずに失敗する。
保存済み計画へ追記する場合は、`atk plans checkout`で`~/.claude/plans`へ取得してから追記し、`atk plans commit`で保存する。
"""

from __future__ import annotations

import argparse
import datetime
import pathlib
import sys
from collections.abc import Callable

try:
    from agent_toolkit._common import next_action as _next_action
    from agent_toolkit._common.atomic_file import atomic_write
    from agent_toolkit._common.markdown_headings import top_level_atx_headings
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


def main(argv: list[str] | None = None) -> int:
    """CLIから進捗ログの追記を開始する。"""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("plan_file", type=pathlib.Path, help="更新する計画ファイル")
    parser.add_argument("--completed-step", required=True, help="完了した工程")
    parser.add_argument("--result", required=True, help="結果・特記事項")
    args = parser.parse_args(argv)
    try:
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

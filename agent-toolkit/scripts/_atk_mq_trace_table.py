#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "filelock",
#   "markdown-it-py[linkify]>=4.0.0",
#   "platformdirs",
#   "pyyaml",
# ]
# ///
"""選抜済みフィードバックと計画の追跡表を生成・検査する。"""

import argparse
import dataclasses
import pathlib
import re
import sys

import _atk_mq_common as _common
import _atk_mq_frontmatter as _frontmatter
import _atk_mq_repo as _repo
import _atk_mq_schedule as _schedule
import markdown_it
from markdown_it.token import Token

_TITLE_RE = re.compile(r"^\s*#+\s*(.+?)\s*$")
_PRESENTATION_FILENAME_RE = re.compile(r"^`([^`]+)`$")
_EXPECTED_ROW_RE = re.compile(r"^\| `(?P<filename>[^`]+)` \| (?P<title>(?:\\.|[^|])*) \| `[0-9a-fA-F]{64}` \|")
_TRACE_TABLE_HEADER = ("ファイル名", "原題", "本文SHA-256", "想定変更対象")


@dataclasses.dataclass(frozen=True)
class TraceRow:
    """追跡表の1行を表す。"""

    filename: str
    title: str
    body_sha256: str
    target_files: tuple[str, ...]


def build_trace_rows(
    private_notes: pathlib.Path,
    target_repo: str,
    filenames: tuple[str, ...],
) -> tuple[TraceRow, ...]:
    """選抜順を保った追跡表行をキュー実ファイルから構築する。"""
    if not filenames:
        raise ValueError("--filenameを1件以上指定してください")
    duplicates = tuple(name for name in dict.fromkeys(filenames) if filenames.count(name) > 1)
    if duplicates:
        raise ValueError(f"ファイル名が重複しています: {', '.join(duplicates)}")

    normalized_repo = _repo._resolve_repo_id(target_repo)  # pylint: disable=protected-access  # noqa: SLF001
    entries = _common._load_schedule_entries(  # pylint: disable=protected-access  # noqa: SLF001
        private_notes,
        normalized_repo,
        _common.MQ_STATES,
    )
    by_filename = {entry.filename: entry for entry in entries}
    rows: list[TraceRow] = []
    for filename in filenames:
        entry = by_filename.get(filename)
        if entry is None:
            raise ValueError(f"対象リポジトリのキューにファイルが存在しません: {filename}")
        parsed = _frontmatter.parse_frontmatter(entry.text)
        if parsed is None:
            raise ValueError(f"frontmatterを解析できません: {filename}")
        metadata = entry.metadata
        if metadata is None:
            raise ValueError(f"分類メタデータが欠落または無効です: {filename}")
        title = _extract_title(parsed[1], filename)
        rows.append(
            TraceRow(
                filename=filename,
                title=title,
                body_sha256=_schedule.body_sha256(entry.text),
                target_files=metadata.target_files,
            )
        )
    return tuple(rows)


def render_trace_table(rows: tuple[TraceRow, ...]) -> str:
    """追跡表行を固定4列のMarkdown表として返す。"""
    lines = [
        "| ファイル名 | 原題 | 本文SHA-256 | 想定変更対象 |",
        "| --- | --- | --- | --- |",
    ]
    for row in rows:
        target_files = "<br>".join(_escape_cell(path) for path in row.target_files)
        lines.append(
            f"| `{_escape_cell(row.filename)}` | {_escape_cell(row.title)} | "
            f"`{_escape_cell(row.body_sha256)}` | {target_files} |"
        )
    return "\n".join(lines)


def check_trace_table(plan_text: str, expected_table: str) -> tuple[str, ...]:
    """背景節の追跡表と提示素材がキュー由来の期待値へ一致するか検査する。"""
    tokens = tuple(markdown_it.MarkdownIt("commonmark").enable("table").parse(plan_text))
    lines = plan_text.splitlines()
    backgrounds = _heading_sections(tokens, lines, level=2, content="背景")
    if len(backgrounds) != 1:
        return (f"フェンス外の`## 背景`節が1件ではありません: 実際={len(backgrounds)}件",)
    background_start, background_end = backgrounds[0]
    actual_tables = [
        ("\n".join(lines[token.map[0] : token.map[1]]).strip(), _table_header_cells(tokens, index))
        for index, token in enumerate(tokens)
        if token.type == "table_open"
        and token.map is not None
        and background_start <= token.map[0]
        and token.map[1] <= background_end
    ]
    expected_table = expected_table.strip()
    trace_tables = [table for table, header in actual_tables if header == _TRACE_TABLE_HEADER]
    errors: list[str] = []
    if len(trace_tables) != 1:
        errors.append(f"背景節のフィードバック追跡表が1件ではありません: 実際={len(trace_tables)}件")
    elif trace_tables[0] != expected_table:
        errors.append("背景節のフィードバック追跡表が期待値と一致しません")

    presentations = [
        section
        for section in _heading_sections(tokens, lines, level=3, content="提示素材")
        if background_start <= section[0] and section[1] <= background_end
    ]
    if len(presentations) != 1:
        errors.append(f"背景節直下の`### 提示素材`が1件ではありません: 実際={len(presentations)}件")
        return tuple(errors)
    expected_rows = _expected_filename_titles(expected_table)
    actual_rows = _presentation_filename_titles(tokens, presentations[0])
    for filename, expected_title in expected_rows:
        actual_titles = actual_rows.get(filename, [])
        if actual_titles != [expected_title]:
            errors.append(
                f"提示素材のファイル名・原題が期待値と一致しません: {filename}、期待={[expected_title]}、実際={actual_titles}"
            )
    unexpected = sorted(set(actual_rows) - {filename for filename, _title in expected_rows})
    if unexpected:
        errors.append(f"提示素材に選抜対象外のファイル名があります: {unexpected}")
    return tuple(errors)


def _table_header_cells(tokens: tuple[Token, ...], table_index: int) -> tuple[str, ...]:
    """table_openに対応するヘッダーセルをMarkdown構造から返す。"""
    cells: list[str] = []
    in_header_cell = False
    for token in tokens[table_index + 1 :]:
        if token.type in {"tbody_open", "table_close"}:
            break
        if token.type == "th_open":
            in_header_cell = True
        elif token.type == "th_close":
            in_header_cell = False
        elif token.type == "inline" and in_header_cell:
            cells.append(token.content.strip())
    return tuple(cells)


def _extract_title(body: str, filename: str) -> str:
    """本文先頭の非空行から、見出し記号を除いた原題を返す。"""
    for line in body.splitlines():
        if not line.strip():
            continue
        match = _TITLE_RE.fullmatch(line)
        return match.group(1).strip() if match is not None else line.strip()
    raise ValueError(f"本文に原題として利用できる行がありません: {filename}")


def _escape_cell(value: str) -> str:
    """Markdown表のセル値を1論理行へエスケープする。"""
    return value.replace("\\", "\\\\").replace("|", "\\|").replace("\r\n", "<br>").replace("\n", "<br>")


def _heading_sections(
    tokens: tuple[Token, ...],
    lines: list[str],
    *,
    level: int,
    content: str,
) -> list[tuple[int, int]]:
    """最上位の指定見出しについて本文の半開行範囲を返す。"""
    sections: list[tuple[int, int]] = []
    for index, token in enumerate(tokens):
        if (
            getattr(token, "type", None) != "heading_open"
            or getattr(token, "tag", None) != f"h{level}"
            or getattr(token, "level", None) != 0
            or getattr(tokens[index + 1], "content", None) != content
            or getattr(token, "map", None) is None
        ):
            continue
        token_map = token.map
        assert token_map is not None
        start = token_map[1]
        end = len(lines)
        for following in tokens[index + 2 :]:
            if (
                getattr(following, "type", None) == "heading_open"
                and getattr(following, "level", None) == 0
                and getattr(following, "map", None) is not None
                and int(following.tag[1:]) <= level
            ):
                following_map = following.map
                assert following_map is not None
                end = following_map[0]
                break
        sections.append((start, end))
    return sections


def _expected_filename_titles(expected_table: str) -> list[tuple[str, str]]:
    """期待表からファイル名と原題を行順で抽出する。"""
    rows: list[tuple[str, str]] = []
    for line in expected_table.splitlines():
        match = _EXPECTED_ROW_RE.match(line)
        if match is None:
            continue
        title = match.group("title").replace(r"\|", "|").replace(r"\\", "\\")
        rows.append((match.group("filename"), title))
    return rows


def _presentation_filename_titles(
    tokens: tuple[Token, ...],
    presentation: tuple[int, int],
) -> dict[str, list[str]]:
    """提示素材節のファイル名と直後のコードフェンス本文から得た原題を返す。"""
    start, end = presentation
    result: dict[str, list[str]] = {}
    for index, token in enumerate(tokens):
        token_map = getattr(token, "map", None)
        if getattr(token, "type", None) != "inline" or token_map is None or token_map[0] < start or token_map[0] >= end:
            continue
        match = _PRESENTATION_FILENAME_RE.fullmatch(token.content.strip())
        if match is None:
            continue
        filename = match.group(1)
        following_fence = next(
            (
                following
                for following in tokens[index + 1 :]
                if getattr(following, "type", None) in {"fence", "heading_open", "inline"}
            ),
            None,
        )
        if (
            following_fence is None
            or following_fence.type != "fence"
            or following_fence.map is None
            or following_fence.map[0] < start
            or following_fence.map[0] >= end
        ):
            result.setdefault(filename, []).append("")
            continue
        result.setdefault(filename, []).append(_extract_title(following_fence.content, filename))
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("generate", "check"):
        command = subparsers.add_parser(name)
        command.add_argument("--target-repo", required=True)
        command.add_argument("--filename", action="append", required=True)
        if name == "check":
            command.add_argument("--plan-file", required=True, type=pathlib.Path)
    return parser


def main() -> int:
    """追跡表を生成または計画内の表を検査する。"""
    args = _parser().parse_args()
    try:
        private_notes = _common.private_notes_path(pathlib.Path.home())
        rows = build_trace_rows(private_notes, args.target_repo, tuple(args.filename))
        table = render_trace_table(rows)
        if args.command == "generate":
            print(table)
            return 0
        plan_path = args.plan_file.expanduser()
        if not plan_path.is_absolute() or not plan_path.is_file():
            raise ValueError(f"--plan-fileには実在する絶対パスを指定してください: {args.plan_file}")
        errors = check_trace_table(plan_path.read_text(encoding="utf-8"), table)
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    for error in errors:
        print(error, file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())

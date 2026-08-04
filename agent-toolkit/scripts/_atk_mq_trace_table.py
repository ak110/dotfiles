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

_PRESENTATION_FILENAME_RE = re.compile(r"^`([^`]+)`$")
_TRACE_TABLE_HEADER = ("ファイル名", "原題", "本文SHA-256", "想定変更対象")


@dataclasses.dataclass(frozen=True)
class TraceRow:
    """追跡表の1行を表す。"""

    filename: str
    title: str
    body_sha256: str
    target_files: tuple[str, ...]
    source_body: str


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
                source_body=parsed[1],
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


def check_trace_table(plan_text: str, expected_rows: tuple[TraceRow, ...]) -> tuple[str, ...]:
    """背景節の追跡表と提示素材がキュー由来の期待値へ一致するか検査する。"""
    expected_table = render_trace_table(expected_rows)
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
    expected_materials = {row.filename: row.source_body.removesuffix("\n") for row in expected_rows}
    actual_materials = _presentation_filename_bodies(tokens, lines, presentations[0], frozenset(expected_materials))
    for filename, expected_body in expected_materials.items():
        actual_bodies = actual_materials.get(filename, [])
        if actual_bodies != [expected_body]:
            errors.append(f"提示素材のファイル名・原文が期待値と一致しません: {filename}、実際の出現数={len(actual_bodies)}")
    unexpected = sorted(set(actual_materials) - set(expected_materials))
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
    """本文先頭の非空行から、CommonMark見出しなら解析済み内容を原題として返す。"""
    for line_index, line in enumerate(body.splitlines()):
        if not line.strip():
            continue
        tokens = markdown_it.MarkdownIt("commonmark").parse(body)
        for index, token in enumerate(tokens):
            if (
                token.type == "heading_open"
                and token.level == 0
                and token.map == [line_index, line_index + 1]
                and token.tag in {"h1", "h2", "h3", "h4", "h5", "h6"}
            ):
                return tokens[index + 1].content.strip()
        return line.strip()
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


def _presentation_filename_bodies(
    tokens: tuple[Token, ...],
    lines: list[str],
    presentation: tuple[int, int],
    expected_filenames: frozenset[str],
) -> dict[str, list[str]]:
    """提示素材節の単独ファイル名ラベルと直後のtextフェンスから原文を返す。"""
    start, end = presentation
    result: dict[str, list[str]] = {}
    blocks = [
        token
        for token in tokens
        if token.level == 0
        and token.map is not None
        and start <= token.map[0] < end
        and (token.type.endswith("_open") or token.type in {"fence", "code_block", "hr", "html_block"})
    ]
    first_label = next(
        (
            index
            for index, block in enumerate(blocks)
            if block.type == "paragraph_open"
            and block.map is not None
            and block.map[1] - block.map[0] == 1
            and (match := _PRESENTATION_FILENAME_RE.fullmatch(lines[block.map[0]].strip())) is not None
            and match.group(1) in expected_filenames
        ),
        len(blocks),
    )
    material_blocks = blocks[first_label:]
    if any(block.type in {"fence", "code_block"} for block in blocks[:first_label]):
        result.setdefault("<不正な提示素材>", []).append("")
    for block in blocks[:first_label]:
        if block.type != "paragraph_open" or block.map is None or block.map[1] - block.map[0] != 1:
            continue
        if _PRESENTATION_FILENAME_RE.fullmatch(lines[block.map[0]].strip()) is not None:
            result.setdefault("<不正な提示素材>", []).append("")
    cursor = material_blocks[0].map[0] if material_blocks and material_blocks[0].map is not None else end
    for index in range(0, len(material_blocks), 2):
        label = material_blocks[index]
        fence = material_blocks[index + 1] if index + 1 < len(material_blocks) else None
        if label.type != "paragraph_open" or label.map is None or label.map[1] - label.map[0] != 1:
            result.setdefault("<不正な提示素材>", []).append("")
            continue
        if any(line.strip() for line in lines[cursor : label.map[0]]):
            result.setdefault("<不正な提示素材>", []).append("")
        match = _PRESENTATION_FILENAME_RE.fullmatch(lines[label.map[0]].strip())
        if match is None:
            result.setdefault("<不正な提示素材>", []).append("")
            continue
        filename = match.group(1)
        if fence is None or fence.map is None or fence.type != "fence" or fence.info.strip() != "text":
            result.setdefault(filename, []).append("")
            continue
        if any(line.strip() for line in lines[label.map[1] : fence.map[0]]):
            result.setdefault("<不正な提示素材>", []).append("")
        result.setdefault(filename, []).append(fence.content.removesuffix("\n"))
        cursor = fence.map[1]
    if any(line.strip() for line in lines[cursor:end]):
        result.setdefault("<不正な提示素材>", []).append("")
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
        errors = check_trace_table(plan_path.read_text(encoding="utf-8"), rows)
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    for error in errors:
        print(error, file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())

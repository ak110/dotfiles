#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["markdown-it-py[linkify]>=4.0.0"]
# ///
"""計画の成立に必要なMarkdown構造と実体だけを検査する。"""

from __future__ import annotations

import argparse
import collections
import pathlib
import re
import subprocess
import sys

import markdown_it
import markdown_it.token

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3] / "scripts"))
import _plan_format  # noqa: E402  # pylint: disable=wrong-import-position,import-error

_H1_RE = re.compile(r"^# (?!#)\S")
_CHECKBOX_RE = re.compile(r"^- \[ \] `([^`]+)`(?P<suffix>.*)$")
_H3_RE = re.compile(r"^### `([^`]+)`(?:\s|（|$)")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$")
_FENCE_RE = re.compile(r"^\s*(`{3,}|~{3,})(.*)$", re.MULTILINE)
_DELETED_RE = re.compile(r"廃止・削除")
_DELETE_WORD_RE = re.compile(r"削除|廃止")
_BASE_VALUE_RE = re.compile(r"`(?:[0-9a-f]{40}|[0-9a-f]{64})`(?:\s.*)?")
_SKILL_CALL_RE = re.compile(r"(?:Skillツールで|スキル)\s*`([^`]+)`")
_AGENT_CALL_RE = re.compile(r"(?:Agentツールで|subagent_type:\s*)`?([A-Za-z0-9:_-]+)`?")
_TABLE_SEPARATOR_RE = re.compile(r"\|?\s*:?-{3,}:?(?:\s*\|\s*:?-{3,}:?)*\s*\|?")
_GENERIC_AGENT_TYPES = frozenset({"claude", "Explore", "Plan"})
_BUG_INVESTIGATION_HEADING_PREFIX = "バグ調査結果:"
_BUG_INVESTIGATION_REQUIRED_ROWS = (
    "観測事象",
    "期待する契約",
    "直接的原因",
    "混入要因",
    "動機的要因",
    "見逃し原因",
    "根本原因",
    "原因分析の根拠",
    "類似見直しの観点",
    "類似見直し結果",
    "是正処置",
    "横展開処置",
    "再発防止処置",
    "設計意図の記録",
)


def _outside_fences(lines: list[str]) -> tuple[list[bool], list[str]]:
    outside: list[bool] = []
    errors: list[str] = []
    marker: str | None = None
    for _number, line in enumerate(lines, start=1):
        match = _FENCE_RE.match(line)
        if marker is None:
            outside.append(True)
            if match:
                marker = match.group(1)
        else:
            outside.append(False)
            if match and match.group(1)[0] == marker[0] and len(match.group(1)) >= len(marker) and not match.group(2).strip():
                marker = None
    if marker is not None:
        errors.append("閉じていないMarkdownフェンスがある")
    return outside, errors


def _section_bounds(lines: list[str], outside: list[bool], title: str) -> tuple[int, int] | None:
    start = next((i for i, line in enumerate(lines) if outside[i] and line == f"## {title}"), None)
    if start is None:
        return None
    end = next((i for i in range(start + 1, len(lines)) if outside[i] and lines[i].startswith("## ")), len(lines))
    return start, end


def _check_h1(lines: list[str], outside: list[bool]) -> list[str]:
    errors: list[str] = []
    first_body_line = next((index for index, line in enumerate(lines) if outside[index] and line.strip()), None)
    if first_body_line is None or not _H1_RE.match(lines[first_body_line]):
        errors.append("Markdown本文の先頭行に正規のH1見出しが無い")
    h1_lines = [i + 1 for i, line in enumerate(lines) if outside[i] and line.startswith("# ")]
    expected_h1_lines = [first_body_line + 1] if first_body_line is not None else []
    if h1_lines != expected_h1_lines:
        errors.append(f"H1見出しが一意でない: {h1_lines}")
    for index in range(1, len(lines)):
        if outside[index] and lines[index].strip() and set(lines[index].strip()) == {"="} and lines[index - 1].strip():
            errors.append(f"Setext形式のH1候補がある: {index + 1}行目")
    return errors


def _check_required_sections(lines: list[str], outside: list[bool], text: str) -> list[str]:
    """必須H2の一意性・順序と固定H3の親節を検査する。"""
    implementation_materials_h2 = _plan_format.resolve_implementation_materials_h2(text)
    required_h2 = tuple(
        implementation_materials_h2 if title == "実装資料" else title for title in _plan_format.PLAN_REQUIRED_H2
    )
    errors: list[str] = []
    positions: list[int] = []
    for title in required_h2:
        matches = [index for index, line in enumerate(lines) if outside[index] and line == f"## {title}"]
        if len(matches) != 1:
            errors.append(f"必須H2`## {title}`が1件必要: 実際={len(matches)}件")
        if matches:
            positions.append(matches[0])
    if len(positions) == len(required_h2) and positions != sorted(positions):
        errors.append("必須H2の順序が計画ファイル完成条件と一致しない")

    fixed_h3 = (("背景", "計画メタ情報"), ("変更内容", "対象ファイル一覧"))
    for parent, child in fixed_h3:
        bounds = _section_bounds(lines, outside, parent)
        count = (
            sum(1 for index in range(bounds[0] + 1, bounds[1]) if outside[index] and lines[index] == f"### {child}")
            if bounds is not None
            else 0
        )
        if count != 1:
            errors.append(f"`## {parent}`直下に`### {child}`が1件必要: 実際={count}件")
    return errors


def _check_plan_metadata(lines: list[str], outside: list[bool]) -> list[str]:
    """背景直下の計画メタ情報について必須4項目の一意性と値を検査する。"""
    bounds = _section_bounds(lines, outside, "背景")
    if bounds is None:
        return ["`## 背景`が無いため計画メタ情報を検査できない"]
    metadata_start = next(
        (index for index in range(bounds[0] + 1, bounds[1]) if outside[index] and lines[index] == "### 計画メタ情報"),
        None,
    )
    if metadata_start is None:
        return ["`## 背景`直下に`### 計画メタ情報`が無い"]
    metadata_end = next(
        (index for index in range(metadata_start + 1, bounds[1]) if outside[index] and lines[index].startswith("### ")),
        bounds[1],
    )
    required = ("起動経路", "対象リポジトリ", "作業種別", "ベースコミット")
    values: dict[str, str] = {}
    errors: list[str] = []
    for field in required:
        matches = []
        pattern = re.compile(rf"^- {re.escape(field)}: (.+)$")
        for index in range(metadata_start + 1, metadata_end):
            if outside[index] and (match := pattern.fullmatch(lines[index])) is not None:
                matches.append(match.group(1).strip())
        if len(matches) != 1:
            errors.append(f"計画メタ情報の`{field}`が1件必要: 実際={len(matches)}件")
        elif not matches[0]:
            errors.append(f"計画メタ情報の`{field}`が空である")
        else:
            values[field] = matches[0]
    if (work_type := values.get("作業種別")) is not None and work_type not in {"バグ対応", "通常変更"}:
        errors.append("計画メタ情報の`作業種別`は`バグ対応`または`通常変更`で記載する")
    if (base := values.get("ベースコミット")) is not None and _BASE_VALUE_RE.fullmatch(base) is None:
        errors.append("計画メタ情報の`ベースコミット`は完全長SHAで記載する")
    return errors


def _extract_targets(
    lines: list[str], outside: list[bool], bounds: tuple[int, int]
) -> tuple[list[tuple[str, str]], list[tuple[str, int]]]:
    start, end = bounds
    checkboxes: list[tuple[str, str]] = []
    headings: list[tuple[str, int]] = []
    for index in range(start + 1, end):
        if not outside[index]:
            continue
        if match := _CHECKBOX_RE.match(lines[index]):
            checkboxes.append((match.group(1), match.group("suffix")))
        if match := _H3_RE.match(lines[index]):
            headings.append((match.group(1), index))
    return checkboxes, headings


def _check_target_structure(lines: list[str], outside: list[bool], work_dir: pathlib.Path) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    bounds = _section_bounds(lines, outside, "変更内容")
    if bounds is None:
        return ["`## 変更内容`が無い"], []
    checkboxes, headings = _extract_targets(lines, outside, bounds)
    if not checkboxes:
        errors.append("対象ファイル一覧の未チェック項目が無い")
    checkbox_paths = [path for path, _ in checkboxes]
    heading_paths = [path for path, _ in headings]
    for label, values in (("対象ファイル一覧", checkbox_paths), ("H3見出し", heading_paths)):
        duplicates = sorted(path for path, count in collections.Counter(values).items() if count > 1)
        if duplicates:
            errors.append(f"{label}に重複したパス: {duplicates}")
    missing_h3 = [path for path in checkbox_paths if path not in heading_paths]
    missing_checkbox = [path for path in heading_paths if path not in checkbox_paths]
    if missing_h3:
        errors.append(f"H3見出しが無い対象ファイル: {missing_h3}")
    if missing_checkbox:
        errors.append(f"対象ファイル一覧に無いH3見出し: {missing_checkbox}")

    end = bounds[1]
    for path, heading_index in headings:
        next_heading = next(
            (
                i
                for i in range(heading_index + 1, end)
                if outside[i] and (match := _HEADING_RE.match(lines[i])) is not None and len(match.group(1)) <= 3
            ),
            end,
        )
        block = "\n".join(lines[heading_index + 1 : next_heading])
        if not _FENCE_RE.search(block):
            errors.append(f"コードブロックが無いH3: {path}")
        suffix = next((suffix for candidate, suffix in checkboxes if candidate == path), "")
        if _DELETED_RE.search(suffix) and not _DELETE_WORD_RE.search(block):
            errors.append(f"削除指示が無い廃止対象: {path}")
        if not re.search(r"新設|廃止・削除", suffix):
            candidate = pathlib.Path(path)
            resolved = candidate if candidate.is_absolute() else work_dir / candidate
            if not resolved.exists():
                errors.append(f"実在確認できないパス: {path}")
    return errors, checkbox_paths


def _table_cell_count(line: str) -> int:
    """表の原文行を、エスケープされていないパイプだけでセルへ分割する。"""
    stripped = line.strip()
    separators: list[int] = []
    preceding_backslashes = 0
    for index, character in enumerate(stripped):
        if character == "\\":
            preceding_backslashes += 1
            continue
        if character == "|" and preceding_backslashes % 2 == 0:
            separators.append(index)
        preceding_backslashes = 0
    count = len(separators) + 1
    if separators and separators[0] == 0:
        count -= 1
    if separators and separators[-1] == len(stripped) - 1:
        count -= 1
    return count


def _table_cells(line: str) -> list[str]:
    """Markdown表の原文行を、エスケープされていないパイプでセルへ分割する。"""
    stripped = line.strip()
    cells: list[str] = []
    current: list[str] = []
    preceding_backslashes = 0
    for character in stripped:
        if character == "\\":
            preceding_backslashes += 1
            current.append(character)
            continue
        if character == "|" and preceding_backslashes % 2 == 0:
            cells.append("".join(current).strip())
            current = []
        else:
            current.append(character)
        preceding_backslashes = 0
    cells.append("".join(current).strip())
    if cells and not cells[0]:
        cells.pop(0)
    if cells and not cells[-1]:
        cells.pop()
    return cells


def _work_type(lines: list[str], outside: list[bool]) -> str | None:
    """計画メタ情報から一意な作業種別を返す。"""
    bounds = _section_bounds(lines, outside, "背景")
    if bounds is None:
        return None
    metadata_start = next(
        (index for index in range(bounds[0] + 1, bounds[1]) if outside[index] and lines[index] == "### 計画メタ情報"),
        None,
    )
    if metadata_start is None:
        return None
    metadata_end = next(
        (index for index in range(metadata_start + 1, bounds[1]) if outside[index] and lines[index].startswith("### ")),
        bounds[1],
    )
    matches = [
        match.group(1)
        for index in range(metadata_start + 1, metadata_end)
        if (line := lines[index])
        if outside[index] and (match := re.fullmatch(r"- 作業種別: (.+)", line)) is not None
    ]
    return matches[0] if len(matches) == 1 else None


def _first_table_rows(
    lines: list[str], outside: list[bool], start: int, end: int
) -> tuple[list[str] | None, list[list[str]] | None]:
    """指定範囲の最初のMarkdown表からヘッダーと本文セルを返す。"""
    for index in range(start, end - 1):
        if not outside[index] or not outside[index + 1]:
            continue
        if "|" not in lines[index] or _TABLE_SEPARATOR_RE.fullmatch(lines[index + 1].strip()) is None:
            continue
        header = _table_cells(lines[index])
        rows: list[list[str]] = []
        for row_index in range(index + 2, end):
            if not outside[row_index] or "|" not in lines[row_index]:
                break
            cells = _table_cells(lines[row_index])
            if cells:
                rows.append(cells)
        return header, rows
    return None, None


def _heading_records(tokens: list[markdown_it.token.Token], outside: list[bool]) -> list[tuple[int, int, str]]:
    """Markdownパーサーが認識したH2・H3の位置、深さ、正規化済み本文を返す。"""
    headings: list[tuple[int, int, str]] = []
    for index, token in enumerate(tokens[:-1]):
        if token.type != "heading_open" or token.tag not in {"h2", "h3"} or token.map is None or token.level != 0:
            continue
        position = token.map[0]
        inline = tokens[index + 1]
        if position >= len(outside) or not outside[position] or inline.type != "inline":
            continue
        headings.append((position, int(token.tag[1]), inline.content.strip()))
    return headings


def _check_bug_investigation_tables(lines: list[str], outside: list[bool], tokens: list[markdown_it.token.Token]) -> list[str]:
    """バグ対応計画にある名前付き調査表を表ごとに検査する。"""
    if _work_type(lines, outside) != "バグ対応":
        return []

    warnings: list[str] = []
    headings: list[tuple[int, str, str]] = []
    legacy_count = 0
    parent_h2 = ""
    structural_headings = _heading_records(tokens, outside)
    for position, level, content in structural_headings:
        if level == 2:
            parent_h2 = content
            continue
        if content == "バグ調査結果":
            legacy_count += 1
            continue
        if content.startswith(_BUG_INVESTIGATION_HEADING_PREFIX):
            name = content.removeprefix(_BUG_INVESTIGATION_HEADING_PREFIX).strip()
            headings.append((position, parent_h2, name))

    if legacy_count:
        warnings.append("旧形式`### バグ調査結果`を名前付き形式`### バグ調査結果: <事象名>`へ移行する")
    if not headings:
        warnings.append("バグ対応計画には名前付きの`### バグ調査結果: <事象名>`が1件以上必要")
        return warnings

    duplicates = sorted(
        name for name, count in collections.Counter(name for _, _, name in headings if name).items() if count > 1
    )
    if duplicates:
        warnings.append(f"バグ調査結果の事象名が重複している: {duplicates}")

    for position, parent, name in headings:
        label = name or "<空名>"
        if not name:
            warnings.append("バグ調査結果の事象名が空である")
        if parent != "背景":
            warnings.append(f"バグ調査結果`{label}`の親H2は`## 背景`である必要がある: 実際=`## {parent}`")
        end = next(
            (heading_position for heading_position, _, _ in structural_headings if heading_position > position),
            len(lines),
        )
        header, table_rows = _first_table_rows(lines, outside, position + 1, end)
        if header is None or table_rows is None:
            warnings.append(f"バグ調査結果`{label}`にMarkdown表が無い")
            continue
        if header != ["項目", "内容"] or any(len(cells) != 2 for cells in table_rows):
            warnings.append(f"バグ調査結果`{label}`の表は`項目`・`内容`の2列である必要がある: 実際={header}")
        rows = [cells[0] for cells in table_rows if cells]
        if rows != list(_BUG_INVESTIGATION_REQUIRED_ROWS):
            warnings.append(
                f"バグ調査結果`{label}`の必須14行と順序が一致しない: 期待={list(_BUG_INVESTIGATION_REQUIRED_ROWS)}, 実際={rows}"
            )
    return warnings


def _check_tables(lines: list[str], tokens: list[markdown_it.token.Token]) -> list[str]:
    """表トークンと段落候補の原文位置から行ごとのセル数を検査する。"""
    errors: list[str] = []
    for token in tokens:
        if token.type == "table_open" and token.map is not None:
            start, end = token.map
            expected = _table_cell_count(lines[start])
            for row in range(start + 2, end):
                actual = _table_cell_count(lines[row])
                if actual != expected:
                    errors.append(f"表のセル数が一致しない: {row + 1}行目")
        if token.type != "paragraph_open" or token.map is None:
            continue
        start, end = token.map
        for row in range(start, end - 1):
            if "|" not in lines[row] or _TABLE_SEPARATOR_RE.fullmatch(lines[row + 1].strip()) is None:
                continue
            expected = _table_cell_count(lines[row])
            actual = _table_cell_count(lines[row + 1])
            if actual != expected:
                errors.append(f"表のセル数が一致しない: {row + 2}行目")
    return errors


def _check_references(tokens: list[markdown_it.token.Token], work_dir: pathlib.Path) -> list[str]:
    """コードブロックを除くインライン本文のスキル・専用agent参照を検査する。"""
    errors: list[str] = []
    inline_text = "\n".join(token.content for token in tokens if token.type == "inline")
    for skill in sorted(set(_SKILL_CALL_RE.findall(inline_text))):
        name = skill.split(":", 1)[-1]
        candidates = [
            work_dir / "agent-toolkit" / "skills" / name / "SKILL.md",
            work_dir / ".claude" / "skills" / name / "SKILL.md",
        ]
        if not any(path.exists() for path in candidates):
            errors.append(f"実在しないスキル参照: {skill}")
    for agent in sorted(set(_AGENT_CALL_RE.findall(inline_text)) - _GENERIC_AGENT_TYPES):
        name = agent.split(":", 1)[-1]
        if not (work_dir / "agent-toolkit" / "agents" / f"{name}.md").exists():
            errors.append(f"実在しないサブエージェント参照: {agent}")
    return errors


def _git_changed_files(work_dir: pathlib.Path, base_commit: str) -> tuple[list[str] | None, str | None]:
    result = subprocess.run(
        ["git", "-C", str(work_dir), "diff", "--name-only", f"{base_commit}..HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return None, result.stderr.strip() or "git diffに失敗した"
    return [line for line in result.stdout.splitlines() if line], None


def check(plan_path: pathlib.Path, work_dir: pathlib.Path, base_commit: str | None) -> tuple[list[str], list[str]]:
    """計画ファイルを検査し、エラーと警告を返す。"""
    text = plan_path.read_text(encoding="utf-8")
    lines = text.splitlines()
    body_start = _plan_format.markdown_body_start_index(text)
    structure_lines = ["" if index < body_start else line for index, line in enumerate(lines)]
    outside, fence_errors = _outside_fences(structure_lines)
    body_lines = {lineno - 1 for lineno, _ in _plan_format.iter_markdown_body_lines(text)}
    outside = [is_outside and index in body_lines for index, is_outside in enumerate(outside)]
    markdown_body = "\n".join(line if outside[index] else "" for index, line in enumerate(lines))
    tokens = markdown_it.MarkdownIt("commonmark").enable("table").parse(markdown_body)
    errors = fence_errors + _check_h1(lines, outside) + _check_required_sections(lines, outside, text)
    errors.extend(_plan_format.check_h2_order(text))
    errors.extend(_check_plan_metadata(lines, outside))
    target_errors, planned_paths = _check_target_structure(lines, outside, work_dir)
    errors.extend(target_errors)
    errors.extend(_check_tables(lines, tokens))
    errors.extend(_check_references(tokens, work_dir))
    warnings = _check_bug_investigation_tables(lines, outside, tokens)
    if base_commit is not None:
        changed, error = _git_changed_files(work_dir, base_commit)
        if error:
            errors.append(error)
        elif sorted(set(changed or ())) != sorted(set(planned_paths)):
            planned = sorted(set(planned_paths))
            actual = sorted(set(changed or ()))
            warnings.append(f"対象ファイル一覧と実変更ファイルが一致しない: 計画={planned}, 実差分={actual}")
    return errors, warnings


def main(argv: list[str] | None = None) -> int:
    """コマンドライン引数を解析して計画検査を実行する。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plan_file", type=pathlib.Path)
    parser.add_argument("--work-dir", type=pathlib.Path, default=pathlib.Path.cwd())
    parser.add_argument("--base-commit")
    try:
        args = parser.parse_args(argv)
        errors, warnings = check(args.plan_file, args.work_dir, args.base_commit)
    except (OSError, UnicodeDecodeError) as error:
        print(f"計画ファイルを読み込めない: {error}", file=sys.stderr)
        return 2
    for error in errors:
        print(error, file=sys.stderr)
    for warning in warnings:
        print(f"[warn] {warning}", file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())

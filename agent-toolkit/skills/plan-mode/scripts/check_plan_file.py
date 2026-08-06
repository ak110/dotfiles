#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""計画の成立に必要なMarkdown構造と実体だけを検査する。"""

from __future__ import annotations

import argparse
import collections
import pathlib
import re
import subprocess
import sys

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
_REQUIRED_H2 = (
    "変更履歴",
    "背景",
    "対応方針",
    "調査結果",
    "変更内容",
    "実行方法",
    "進捗ログ",
    "計画ファイル（本ファイル）のパス",
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
    if not lines or not _H1_RE.match(lines[0]):
        errors.append("先頭行に正規のH1見出しが無い")
    h1_lines = [i + 1 for i, line in enumerate(lines) if outside[i] and line.startswith("# ")]
    if h1_lines != [1]:
        errors.append(f"H1見出しが一意でない: {h1_lines}")
    for index in range(1, len(lines)):
        if outside[index] and lines[index].strip() and set(lines[index].strip()) == {"="} and lines[index - 1].strip():
            errors.append(f"Setext形式のH1候補がある: {index + 1}行目")
    return errors


def _check_required_sections(lines: list[str], outside: list[bool]) -> list[str]:
    """必須H2の一意性・順序と固定H3の親節を検査する。"""
    errors: list[str] = []
    positions: list[int] = []
    for title in _REQUIRED_H2:
        matches = [index for index, line in enumerate(lines) if outside[index] and line == f"## {title}"]
        if len(matches) != 1:
            errors.append(f"必須H2`## {title}`が1件必要: 実際={len(matches)}件")
        if matches:
            positions.append(matches[0])
    if len(positions) == len(_REQUIRED_H2) and positions != sorted(positions):
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


def _check_tables(lines: list[str], outside: list[bool]) -> list[str]:
    errors: list[str] = []
    for index in range(len(lines) - 1):
        if not outside[index] or not outside[index + 1] or "|" not in lines[index]:
            continue
        separator = lines[index + 1].strip()
        if not re.fullmatch(r"\|?\s*:?-{3,}:?(?:\s*\|\s*:?-{3,}:?)+\s*\|?", separator):
            continue
        expected = len(lines[index].strip().strip("|").split("|"))
        row = index + 2
        while row < len(lines) and outside[row] and "|" in lines[row] and lines[row].strip():
            actual = len(lines[row].strip().strip("|").split("|"))
            if actual != expected:
                errors.append(f"表のセル数が一致しない: {row + 1}行目")
            row += 1
    return errors


def _check_references(text: str, work_dir: pathlib.Path) -> list[str]:
    errors: list[str] = []
    for skill in sorted(set(_SKILL_CALL_RE.findall(text))):
        name = skill.split(":", 1)[-1]
        candidates = [
            work_dir / "agent-toolkit" / "skills" / name / "SKILL.md",
            work_dir / ".claude" / "skills" / name / "SKILL.md",
        ]
        if not any(path.exists() for path in candidates):
            errors.append(f"実在しないスキル参照: {skill}")
    for agent in sorted(set(_AGENT_CALL_RE.findall(text))):
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
    outside, fence_errors = _outside_fences(lines)
    errors = fence_errors + _check_h1(lines, outside) + _check_required_sections(lines, outside)
    errors.extend(_check_plan_metadata(lines, outside))
    target_errors, planned_paths = _check_target_structure(lines, outside, work_dir)
    errors.extend(target_errors)
    errors.extend(_check_tables(lines, outside))
    errors.extend(_check_references(text, work_dir))
    warnings: list[str] = []
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

#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["markdown-it-py[linkify]>=4.0.0"]
# ///
"""計画の成立に必要な情報契約と実体だけを検査する。"""

from __future__ import annotations

import argparse
import collections
import pathlib
import re
import subprocess
import sys

_PLUGIN_ROOT = pathlib.Path(__file__).resolve().parents[3]

sys.path.insert(0, str(_PLUGIN_ROOT / "scripts"))
import _plan_format  # noqa: E402  # pylint: disable=wrong-import-position,import-error

_FENCE_RE = re.compile(r"^\s*(`{3,}|~{3,})(.*)$", re.MULTILINE)
_BASE_VALUE_RE = re.compile(r"`?([0-9a-f]{40}|[0-9a-f]{64})`?")
_SKILL_CALL_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?:Skillツールで|スキル)\s*`([^`]+)`"),
    re.compile(r"`(agent-toolkit:[A-Za-z0-9][A-Za-z0-9_-]*)`(?:スキル)?を(?:起動|呼び出)"),
)
_AGENT_CALL_RE = re.compile(r"(?:Agentツールで|subagent_type:\s*)`?([A-Za-z0-9:_-]+)`?")
_GENERIC_AGENT_TYPES = frozenset({"claude", "Explore", "Plan"})


def _outside_fences(lines: list[str]) -> tuple[list[bool], list[str]]:
    """Markdownフェンス外の行と未閉鎖フェンスのエラーを返す。"""
    outside: list[bool] = []
    marker: str | None = None
    for line in lines:
        match = _FENCE_RE.match(line)
        if marker is None:
            outside.append(True)
            if match:
                marker = match.group(1)
        else:
            outside.append(False)
            if match and match.group(1)[0] == marker[0] and len(match.group(1)) >= len(marker) and not match.group(2).strip():
                marker = None
    errors = ["閉じていないMarkdownフェンスがある"] if marker is not None else []
    return outside, errors


def _section_bounds(lines: list[str], outside: list[bool], title: str) -> tuple[int, int] | None:
    """指定H2の開始位置と次のH2の位置を返す。"""
    start = next((index for index, line in enumerate(lines) if outside[index] and line == f"## {title}"), None)
    if start is None:
        return None
    end = next(
        (index for index in range(start + 1, len(lines)) if outside[index] and lines[index].startswith("## ")), len(lines)
    )
    return start, end


def _h3_bounds(lines: list[str], outside: list[bool], parent: str, child: str) -> tuple[int, int] | None:
    """指定H2直下に一意に存在するH3の本文範囲を返す。"""
    parent_bounds = _section_bounds(lines, outside, parent)
    if parent_bounds is None:
        return None
    matches = [
        index for index in range(parent_bounds[0] + 1, parent_bounds[1]) if outside[index] and lines[index] == f"### {child}"
    ]
    if len(matches) != 1:
        return None
    start = matches[0]
    end = next(
        (
            index
            for index in range(start + 1, parent_bounds[1])
            if outside[index] and (lines[index].startswith("## ") or lines[index].startswith("### "))
        ),
        parent_bounds[1],
    )
    return start, end


def _check_required_sections(lines: list[str], outside: list[bool], text: str) -> list[str]:
    """意味アンカーと実装契約内の固定H3が一意かを検査する。"""
    errors = _plan_format.check_h2_order(text)
    for child in ("計画メタ情報", "対象ファイル一覧"):
        bounds = _section_bounds(lines, outside, "実装契約")
        count = (
            sum(1 for index in range(bounds[0] + 1, bounds[1]) if outside[index] and lines[index] == f"### {child}")
            if bounds is not None
            else 0
        )
        if count != 1:
            errors.append(f"`## 実装契約`直下に`### {child}`が1件必要: 実際={count}件")
    return errors


def _metadata_values(lines: list[str], outside: list[bool]) -> tuple[dict[str, str], list[str]]:
    """計画メタ情報の値と構造エラーを返す。"""
    bounds = _h3_bounds(lines, outside, "実装契約", "計画メタ情報")
    if bounds is None:
        return {}, ["`## 実装契約`直下の`### 計画メタ情報`を検査できない"]
    required = ("対象リポジトリ", "ベースコミット", "作業種別")
    values: dict[str, str] = {}
    errors: list[str] = []
    for field in required:
        pattern = re.compile(rf"^- {re.escape(field)}: (.+)$")
        matches = [
            match.group(1).strip()
            for index in range(bounds[0] + 1, bounds[1])
            if outside[index] and (match := pattern.fullmatch(lines[index])) is not None
        ]
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
    return values, errors


def _git_path_exists(work_dir: pathlib.Path, base_commit: str, path: str) -> tuple[bool | None, str | None]:
    """基準コミットにパスが存在するかを返す。"""
    result = subprocess.run(
        ["git", "-C", str(work_dir), "cat-file", "-e", f"{base_commit}:{path}"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode == 0:
        return True, None
    if result.returncode == 128:
        return False, None
    return None, result.stderr.strip() or f"基準コミット上のパス確認に失敗した: {path}"


def _git_commit_exists(work_dir: pathlib.Path, base_commit: str) -> bool:
    """完全長SHAが対象リポジトリのcommitとして解決できるかを返す。"""
    result = subprocess.run(
        ["git", "-C", str(work_dir), "cat-file", "-e", f"{base_commit}^{{commit}}"],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0


def _git_root(work_dir: pathlib.Path) -> tuple[pathlib.Path | None, str | None]:
    """作業ディレクトリが属するGitルートの正規化済みパスを返す。"""
    result = subprocess.run(
        ["git", "-C", str(work_dir), "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return None, result.stderr.strip() or "作業ディレクトリのGitルートを解決できない"
    return pathlib.Path(result.stdout.strip()).resolve(), None


def _check_target_repo(declared_value: str | None, work_dir: pathlib.Path) -> list[str]:
    """宣言された対象リポジトリと作業ディレクトリのGitルートを照合する。"""
    if declared_value is None:
        return []
    declared_text = declared_value[1:-1] if declared_value.startswith("`") and declared_value.endswith("`") else declared_value
    declared_path = pathlib.Path(declared_text).expanduser()
    if not declared_path.is_absolute():
        declared_path = work_dir / declared_path
    declared_root = declared_path.resolve()
    actual_root, error = _git_root(work_dir)
    if error is not None:
        return [error]
    if declared_root != actual_root:
        return [
            f"計画メタ情報の対象リポジトリが作業ディレクトリのGitルートと一致しない: 計画={declared_root}, 実際={actual_root}"
        ]
    return []


def _check_targets(text: str, work_dir: pathlib.Path, base_commit: str | None) -> tuple[list[str], list[str]]:
    """対象一覧の構造、パス安全性、基準コミット上の状態を検査する。"""
    targets = _plan_format.extract_plan_targets(text)
    errors: list[str] = []
    invalid_entries = _plan_format.find_invalid_target_entries(text)
    if invalid_entries:
        errors.append(f"対象ファイル一覧に契約形式と一致しない箇条書きがある: {invalid_entries}")
    if not targets:
        errors.append("対象ファイル一覧が空である")
        return errors, []
    paths = [target.path for target in targets]
    duplicates = sorted(path for path, count in collections.Counter(paths).items() if count > 1)
    if duplicates:
        errors.append(f"対象ファイル一覧に重複したパス: {duplicates}")
    invalid = _plan_format.find_invalid_target_file_paths(text)
    if invalid:
        errors.append(f"対象ファイル一覧に危険なパスがある: {invalid}")
    if base_commit is not None:
        for target in targets:
            exists, error = _git_path_exists(work_dir, base_commit, target.path)
            if error is not None:
                errors.append(error)
            elif target.state == "new" and exists:
                errors.append(f"新設対象が基準コミットに実在する: {target.path}")
            elif target.state in {"existing", "deleted"} and not exists:
                label = "削除" if target.state == "deleted" else "既存"
                errors.append(f"{label}対象が基準コミットに実在しない: {target.path}")
    return errors, paths


def _check_references(text: str, work_dir: pathlib.Path) -> list[str]:
    """コードフェンスを除く本文のスキル・専用agent参照を検査する。"""
    inline_text = "\n".join(line for _, line in _plan_format.iter_markdown_body_lines(text))
    errors: list[str] = []
    agent_calls = set(_AGENT_CALL_RE.findall(inline_text)) - _GENERIC_AGENT_TYPES
    skill_calls = {skill for pattern in _SKILL_CALL_PATTERNS for skill in pattern.findall(inline_text)} - agent_calls
    for skill in sorted(skill_calls):
        namespace, separator, qualified_name = skill.partition(":")
        if separator and namespace != "agent-toolkit":
            errors.append(f"実在しないスキル参照: {skill}")
            continue
        name = qualified_name if separator else namespace
        plugin_candidates = (_PLUGIN_ROOT / "skills" / name / "SKILL.md",)
        project_candidates = (
            work_dir / ".claude" / "skills" / name / "SKILL.md",
            work_dir / ".agents" / "skills" / name / "SKILL.md",
        )
        candidates = plugin_candidates if separator else plugin_candidates + project_candidates
        if not any(path.exists() for path in candidates):
            errors.append(f"実在しないスキル参照: {skill}")
    for agent in sorted(agent_calls):
        namespace, separator, qualified_name = agent.partition(":")
        if separator and namespace != "agent-toolkit":
            errors.append(f"実在しないサブエージェント参照: {agent}")
            continue
        name = qualified_name if separator else namespace
        plugin_candidates = (_PLUGIN_ROOT / "agents" / f"{name}.md",)
        project_candidates = (work_dir / ".claude" / "agents" / f"{name}.md",)
        candidates = plugin_candidates if separator else plugin_candidates + project_candidates
        if not any(path.exists() for path in candidates):
            errors.append(f"実在しないサブエージェント参照: {agent}")
    return errors


def _git_changed_files(work_dir: pathlib.Path, base_commit: str) -> tuple[list[str] | None, str | None]:
    """基準コミットからHEADまでに変わったパスを返す。"""
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
    outside, errors = _outside_fences(structure_lines)
    body_lines = {lineno - 1 for lineno, _ in _plan_format.iter_markdown_body_lines(text)}
    outside = [is_outside and index in body_lines for index, is_outside in enumerate(outside)]
    errors.extend(_check_required_sections(lines, outside, text))
    metadata, metadata_errors = _metadata_values(lines, outside)
    errors.extend(metadata_errors)
    errors.extend(_check_target_repo(metadata.get("対象リポジトリ"), work_dir))
    declared_base = metadata.get("ベースコミット")
    normalized_base = _BASE_VALUE_RE.fullmatch(declared_base or "")
    target_base = base_commit or (normalized_base.group(1) if normalized_base is not None else None)
    if target_base is not None and not _git_commit_exists(work_dir, target_base):
        errors.append(f"対象リポジトリでベースコミットを解決できない: {target_base}")
        target_base = None
    target_errors, planned_paths = _check_targets(text, work_dir, target_base)
    errors.extend(target_errors)
    errors.extend(_check_references(text, work_dir))
    if base_commit is not None:
        changed, error = _git_changed_files(work_dir, base_commit)
        if error is not None:
            errors.append(error)
        elif sorted(set(changed or ())) != sorted(set(planned_paths)):
            errors.append(
                f"対象ファイル一覧と実変更ファイルが一致しない: 計画={sorted(set(planned_paths))}, "
                f"実差分={sorted(set(changed or ()))}"
            )
    return errors, []


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

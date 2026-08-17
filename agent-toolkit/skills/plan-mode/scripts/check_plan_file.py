#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["markdown-it-py[linkify]>=4.0.0"]
# ///
"""計画の成立に必要な情報契約と実体だけを検査する。

計画メタ情報、見出し構造、スキル・サブエージェント参照を共有parserで検査する。
"""

from __future__ import annotations

import argparse
import pathlib
import re
import subprocess
import sys

_PLUGIN_ROOT = pathlib.Path(__file__).resolve().parents[3]

sys.path.insert(0, str(_PLUGIN_ROOT / "scripts"))
import _plan_format  # noqa: E402  # pylint: disable=wrong-import-position,import-error

_FENCE_RE = re.compile(r"^\s*(`{3,}|~{3,})(.*)$", re.MULTILINE)
_INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")
_SKILL_TOOL_PREFIX_RE = re.compile(r"Skillツールで$")
_SKILL_NOUN_PREFIX_RE = re.compile(r"スキル$")
_SKILL_SUFFIX_MARKER_RE = re.compile(r"^スキルを(?:起動|呼び出)")
_DIRECT_INVOCATION_RE = re.compile(r"^を(?:起動|呼び出)")
_AGENT_CALL_RE = re.compile(r"(?:Agentツールで|subagent_type:\s*)`?([A-Za-z0-9:_-]+)`?")
_GENERIC_AGENT_TYPES = frozenset({"claude", "Explore", "Plan"})
_BASE_VALUE_RE = re.compile(r"`?([0-9a-f]{40}|[0-9a-f]{64})`?")
# 計画の分量を警告する行数の閾値。
# 既存計画380件の実測分布（中央値443行、第75百分位732行、第90百分位1312行、最大3476行）の
# 第75百分位と第90百分位の間から選び、通常規模の計画を警告せず肥大した計画だけを検出する。
# 閾値を超えても計画として成立し得るため、エラーではなく警告に留める。
_PLAN_LINE_WARNING_THRESHOLD = 1200


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


def _git_commit_exists(work_dir: pathlib.Path, base_commit: str) -> bool:
    """完全長SHAが対象リポジトリのcommitとして解決できるかを返す。"""
    result = subprocess.run(
        ["git", "-C", str(work_dir), "cat-file", "-e", f"{base_commit}^{{commit}}"],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0


def _check_base_commit(declared_value: str | None, work_dir: pathlib.Path) -> list[str]:
    """計画メタ情報のベースコミットを対象リポジトリのcommitとして解決できるかを検査する。

    値の取得元は計画メタ情報だけとし、CLIオプションからは受け取らない。
    HEADとの一致は求めない。計画作成後にHEADが進む正常な経路を拒否しないためである。
    完全長SHAでない値は共有構造検査が書式違反として報告するため、ここでは扱わない。
    """
    if declared_value is None:
        return []
    match = _BASE_VALUE_RE.fullmatch(declared_value)
    if match is None:
        return []
    base_commit = match.group(1)
    if not _git_commit_exists(work_dir, base_commit):
        return [f"対象リポジトリでベースコミットを解決できない: {base_commit}"]
    return []


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


def _check_references(text: str, work_dir: pathlib.Path) -> list[str]:
    """コードフェンスを除く本文のスキル・専用agent参照を検査する。"""
    inline_text = "\n".join(line for _, line in _plan_format.iter_markdown_body_lines(text))
    errors: list[str] = []
    agent_calls = set(_AGENT_CALL_RE.findall(inline_text)) - _GENERIC_AGENT_TYPES
    skill_calls = _classify_skill_references(inline_text) - agent_calls
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


def _classify_skill_references(text: str) -> set[str]:
    """起動又は呼び出しを指示するスキル参照だけを返す。"""
    references: set[str] = set()
    for match in _INLINE_CODE_RE.finditer(text):
        reference = match.group(1)
        line_start = text.rfind("\n", 0, match.start()) + 1
        line_end = text.find("\n", match.end())
        suffix = text[match.end() : None if line_end < 0 else line_end].lstrip()
        prefix = text[line_start : match.start()].rstrip()
        has_tool_prefix = _SKILL_TOOL_PREFIX_RE.search(prefix) is not None
        has_suffix = _SKILL_SUFFIX_MARKER_RE.match(suffix) is not None
        has_noun_prefix = _SKILL_NOUN_PREFIX_RE.search(prefix) is not None
        has_direct_invocation = _DIRECT_INVOCATION_RE.match(suffix) is not None
        has_marker = has_tool_prefix or has_suffix or (has_noun_prefix and has_direct_invocation)
        is_direct_plugin_call = reference.startswith("agent-toolkit:") and has_direct_invocation
        if has_marker or is_direct_plugin_call:
            references.add(reference)
    return references


def _check_plan_size(lines: list[str]) -> list[str]:
    """計画の行数が閾値を超える場合に警告を返す。"""
    if len(lines) <= _PLAN_LINE_WARNING_THRESHOLD:
        return []
    return [
        f"計画の行数が閾値を超えている: {len(lines)}行（閾値{_PLAN_LINE_WARNING_THRESHOLD}行）。"
        "重複する記述を単一の情報源へ集約し、実装工程の入力として参照する素材を外部ファイルへ分けることを検討する"
    ]


def check(plan_path: pathlib.Path, work_dir: pathlib.Path) -> tuple[list[str], list[str]]:
    """計画ファイルを検査し、エラーと警告を返す。"""
    text = plan_path.read_text(encoding="utf-8")
    lines = text.splitlines()
    body_start = _plan_format.markdown_body_start_index(text)
    structure_lines = ["" if index < body_start else line for index, line in enumerate(lines)]
    _outside, errors = _outside_fences(structure_lines)
    errors.extend(_plan_format.check_plan_structure(text))
    parsed, _ambiguity_errors = _plan_format.parse_plan_metadata(text)
    metadata = parsed.values if parsed is not None else {}
    errors.extend(_check_target_repo(metadata.get("対象リポジトリ"), work_dir))
    errors.extend(_check_base_commit(metadata.get("ベースコミット"), work_dir))
    errors.extend(_check_references(text, work_dir))
    return errors, _check_plan_size(lines)


def main(argv: list[str] | None = None) -> int:
    """コマンドライン引数を解析して計画検査を実行する。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plan_file", type=pathlib.Path)
    parser.add_argument("--work-dir", type=pathlib.Path, default=pathlib.Path.cwd())
    try:
        args = parser.parse_args(argv)
        errors, warnings = check(args.plan_file, args.work_dir)
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

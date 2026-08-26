#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["markdown-it-py[linkify]>=4.0.0"]
# ///
"""計画の成立に必要な情報契約と実体だけを検査する。

計画メタ情報、見出し構造、提示素材の新旧形式、スキル・サブエージェント参照を共有parserで検査する。
旧形式は読み取り互換で受理するが、新形式への移行をwarningで案内する。
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


def _detail_path_for(plan_path: pathlib.Path) -> pathlib.Path:
    """メイン側計画パスから対応するdetail側の絶対パスを返す（stem導出）。"""
    return plan_path.with_name(f"{plan_path.stem}{_plan_format.PLAN_DETAIL_SUFFIX}")


def _check_detail_reference(declared_value: str | None, detail_path: pathlib.Path) -> list[str]:
    """メイン側の計画メタ情報`実装詳細`がstem導出値と一致するかを検査する。"""
    if declared_value is None:
        return [f"計画メタ情報の`{_plan_format.PLAN_METADATA_DETAIL_FIELD}`が無い: 期待={detail_path.name}"]
    declared_text = declared_value[1:-1] if declared_value.startswith("`") and declared_value.endswith("`") else declared_value
    if declared_text != detail_path.name:
        return [
            f"計画メタ情報の`{_plan_format.PLAN_METADATA_DETAIL_FIELD}`がstem導出値と一致しない: "
            f"計画={declared_text}, 期待={detail_path.name}"
        ]
    return []


def _main_path_for_detail(detail_path: pathlib.Path) -> pathlib.Path:
    """detail側パスからstem対応するメイン側計画パスを返す。"""
    suffix = _plan_format.PLAN_DETAIL_SUFFIX
    if detail_path.name.endswith(suffix):
        return detail_path.with_name(f"{detail_path.name[: -len(suffix)]}.md")
    return detail_path.with_suffix(".md")


def _check_bug_file_reference(plan_path: pathlib.Path, text: str, work_type: str | None) -> list[str]:
    """バグ対応計画の分離先参照について実在、stem、構造を検査する。"""
    if work_type != "バグ対応":
        return []
    reference = _plan_format.extract_bug_file_reference(text)
    if reference is None:
        return []

    reference_path = pathlib.Path(reference)
    if not reference_path.is_absolute():
        return [f"バグ調査ファイルの参照パスは絶対パスにする: {reference}"]

    expected_path = plan_path.with_name(f"{plan_path.stem}.bugs.md")
    if reference_path.resolve() != expected_path.resolve():
        return [f"バグ調査ファイルの参照パスが計画stemと一致しない: 計画={reference_path}, 期待={expected_path}"]
    if not reference_path.is_file():
        return [f"バグ調査ファイルが実在しない: {reference_path}"]

    bug_text = reference_path.read_text(encoding="utf-8")
    return _plan_format.check_bug_file_structure(bug_text)


def _legacy_action_warnings(text: str) -> list[str]:
    """旧3列表の実施内容表を新4列表へ移行するwarningを返す。"""
    if not _plan_format.has_legacy_action_table(text):
        return []
    return ["実施内容表が旧3列表である。新規作成・改訂では4列表へ移行する"]


def _legacy_bug_warnings(text: str) -> list[str]:
    """旧形式の本文内バグ調査表を分離先ファイルへ移行するwarningを返す。"""
    if not _plan_format.has_legacy_bug_table(text):
        return []
    return ["バグ調査結果が旧形式の本文内表である。新規作成・改訂ではバグ調査ファイルへ移行する"]


def _check_new_format(detail_path: pathlib.Path, text: str, work_dir: pathlib.Path) -> tuple[list[str], list[str]]:
    """新書式（メイン側・detail側の2ファイル）を検査してエラーと警告を返す。

    呼び出し元の`check`は`detail_path.is_file()`が真の場合だけ本関数を呼ぶため、
    detail側の実在は呼び出し前提として扱う。
    """
    errors: list[str] = []
    warnings: list[str] = []
    work_type, main_errors = _plan_format.check_plan_main_structure(text)
    errors.extend(main_errors)

    parsed, _ambiguity_errors = _plan_format.parse_plan_metadata(text)
    metadata = parsed.values if parsed is not None else {}
    errors.extend(_check_detail_reference(metadata.get(_plan_format.PLAN_METADATA_DETAIL_FIELD), detail_path))

    detail_text = detail_path.read_text(encoding="utf-8")
    detail_lines = detail_text.splitlines()
    detail_body_start = _plan_format.markdown_body_start_index(detail_text)
    detail_structure_lines = ["" if index < detail_body_start else line for index, line in enumerate(detail_lines)]
    _outside_detail, detail_fence_errors = _outside_fences(detail_structure_lines)
    errors.extend(detail_fence_errors)
    errors.extend(_plan_format.check_plan_detail_structure(detail_text, work_type))
    errors.extend(_check_bug_file_reference(_main_path_for_detail(detail_path), detail_text, work_type))
    errors.extend(_check_references(detail_text, work_dir))
    warnings.extend(_check_plan_size(detail_lines))
    warnings.extend(_legacy_bug_warnings(detail_text))

    materials, _material_errors = _plan_format.parse_plan_materials(text)
    warnings.extend(_legacy_action_warnings(text))
    if not _plan_format.has_human_action_table(text):
        warnings.append("二ファイル計画が旧ID形式である。新規作成・改訂では人間向け書式へ移行する")
    if materials is not None and materials.is_legacy:
        warnings.append("提示素材が旧形式である。新規作成・改訂では素材表と要求表へ移行する")
    errors.extend(_check_target_repo(metadata.get("対象リポジトリ"), work_dir))
    errors.extend(_check_references(text, work_dir))
    warnings.extend(_check_plan_size(text.splitlines()))
    return errors, warnings


def _check_legacy_format(plan_path: pathlib.Path, text: str, work_dir: pathlib.Path) -> tuple[list[str], list[str]]:
    """旧形式（単一ファイル9節）を検査してエラーと警告を返す。読み取り互換であり新規作成では生成しない。"""
    lines = text.splitlines()
    errors = _plan_format.check_plan_structure(text)
    materials, _material_errors = _plan_format.parse_plan_materials(text)
    warnings: list[str] = []
    warnings.extend(_legacy_action_warnings(text))
    warnings.extend(_legacy_bug_warnings(text))
    if materials is not None and materials.is_legacy:
        warnings.append("提示素材が旧形式である。新規作成・改訂では素材表と要求表へ移行する")
    parsed, _ambiguity_errors = _plan_format.parse_plan_metadata(text)
    metadata = parsed.values if parsed is not None else {}
    errors.extend(_check_target_repo(metadata.get("対象リポジトリ"), work_dir))
    errors.extend(_check_bug_file_reference(plan_path, text, metadata.get("作業種別")))
    errors.extend(_check_references(text, work_dir))
    warnings.extend(_check_plan_size(lines))
    return errors, warnings


def check(plan_path: pathlib.Path, work_dir: pathlib.Path) -> tuple[list[str], list[str]]:
    """計画ファイルを検査し、エラーと警告を返す。

    新旧の判別は対応する`<stem>.detail.md`ファイルの実在で行う（`plan-file-standards.md`正本）。
    実在する場合は新書式の2ファイル、実在しない場合は旧形式の単一ファイルとして検査する。
    """
    text = plan_path.read_text(encoding="utf-8")
    lines = text.splitlines()
    body_start = _plan_format.markdown_body_start_index(text)
    structure_lines = ["" if index < body_start else line for index, line in enumerate(lines)]
    _outside, errors = _outside_fences(structure_lines)

    detail_path = _detail_path_for(plan_path)
    if detail_path.is_file():
        format_errors, warnings = _check_new_format(detail_path, text, work_dir)
    else:
        format_errors, warnings = _check_legacy_format(plan_path, text, work_dir)
    errors.extend(format_errors)
    return errors, warnings


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

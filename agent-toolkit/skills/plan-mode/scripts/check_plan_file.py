#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["markdown-it-py[linkify]>=4.0.0"]
# ///
"""計画の成立に必要な情報契約と実体だけを検査する。

既存計画の`## 実装契約`配置と旧`## 背景`配置は読み取り互換として扱い、
計画メタ情報の配置が複数に分かれる計画は曖昧として拒否する。
`## 対応方針`を持たない既存計画では`## 実装契約`配下を実装者向け領域として読み、
`### 対象ファイル一覧`の取得と2系統のPreToolUseの判定も同じ結果を使う。
"""

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
_INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")
_SKILL_PREFIX_MARKER_RE = re.compile(r"(?:Skillツールで|スキル)$")
_SKILL_SUFFIX_MARKER_RE = re.compile(r"^スキルを(?:起動|呼び出)")
_DIRECT_INVOCATION_RE = re.compile(r"^を(?:起動|呼び出)")
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


def _git_path_object_type(work_dir: pathlib.Path, base_commit: str, path: str) -> tuple[str | None, str | None]:
    """基準コミット上のtree entryが宣言するGit object typeを返す。"""
    result = subprocess.run(
        [
            "git",
            "-C",
            str(work_dir),
            "--literal-pathspecs",
            "ls-tree",
            "--format=%(objectmode) %(objecttype)",
            base_commit,
            "--",
            path,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return None, result.stderr.strip() or f"基準コミット上のパス確認に失敗した: {path}"
    entries = result.stdout.splitlines()
    if not entries:
        return None, None
    if len(entries) != 1 or len(parts := entries[0].split()) != 2:
        return None, f"基準コミット上のパス確認が不正な結果を返した: {path}"
    _, object_type = parts
    return object_type, None


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
        errors.append("対象ファイル一覧が空である（実装者向け領域が新しいH2から始まっていない場合も本エラーになる）")
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
            object_type, error = _git_path_object_type(work_dir, base_commit, target.path)
            if error is not None:
                errors.append(error)
            elif target.state == "new" and object_type is not None:
                errors.append(f"新設対象が基準コミットに実在する: {target.path}")
            elif target.state in {"existing", "deleted"} and object_type is None:
                label = "削除" if target.state == "deleted" else "既存"
                errors.append(f"{label}対象が基準コミットに実在しない: {target.path}")
            elif target.state in {"existing", "deleted"} and object_type not in {"blob", "commit"}:
                label = "削除" if target.state == "deleted" else "既存"
                errors.append(
                    f"{label}対象が基準コミット上のファイルまたはgitlinkではない: {target.path} (object type={object_type})"
                )
    return errors, paths


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
    """明示標識または現plugin namespaceを持つスキル参照を返す。"""
    references: set[str] = set()
    for match in _INLINE_CODE_RE.finditer(text):
        reference = match.group(1)
        line_start = text.rfind("\n", 0, match.start()) + 1
        line_end = text.find("\n", match.end())
        suffix = text[match.end() : None if line_end < 0 else line_end].lstrip()
        prefix = text[line_start : match.start()].rstrip()
        has_marker = _SKILL_PREFIX_MARKER_RE.search(prefix) is not None or _SKILL_SUFFIX_MARKER_RE.match(suffix) is not None
        is_direct_plugin_call = reference.startswith("agent-toolkit:") and _DIRECT_INVOCATION_RE.match(suffix) is not None
        if has_marker or is_direct_plugin_call:
            references.add(reference)
    return references


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
    _outside, errors = _outside_fences(structure_lines)
    errors.extend(_plan_format.check_plan_structure(text))
    parsed, _ambiguity_errors = _plan_format.parse_plan_metadata(text)
    metadata = parsed.values if parsed is not None else {}
    errors.extend(_check_target_repo(metadata.get("対象リポジトリ"), work_dir))
    normalized_base = _BASE_VALUE_RE.fullmatch(metadata.get("ベースコミット") or "")
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
                f"対象ファイル一覧と{base_commit}..HEADのコミット済み差分が一致しない"
                f"（未コミットの作業ツリー差分は照合対象外）: 計画={sorted(set(planned_paths))}, "
                f"実差分={sorted(set(changed or ()))}"
            )
    return errors, []


def main(argv: list[str] | None = None) -> int:
    """コマンドライン引数を解析して計画検査を実行する。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plan_file", type=pathlib.Path)
    parser.add_argument("--work-dir", type=pathlib.Path, default=pathlib.Path.cwd())
    parser.add_argument(
        "--base-commit",
        help=(
            "対象ファイル一覧と`<base>..HEAD`のコミット済み差分を照合する。"
            "未コミットの作業ツリー差分は照合対象に含めないため、実装コミットの作成後に指定する。"
            "起草直後の初版検査では指定しない。"
        ),
    )
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

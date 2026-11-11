"""計画本文のスキル・エージェント参照と対象リポジトリの検査。"""

from __future__ import annotations

import difflib
import pathlib
import re

from agent_toolkit._common import next_action as _next_action
from agent_toolkit._git import command as _git_command
from agent_toolkit._plan import locations, structure

_PLUGIN_DIR = pathlib.Path(locations.__file__).resolve().parents[2]


TARGET_REPO_NEXT_ACTION = "計画の対象worktreeから再実行するか、--work-dirへ計画の対象worktreeの絶対パスを指定して再実行する"


_INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")


_SKILL_TOOL_PREFIX_RE = re.compile(r"Skillツールで$")


_SKILL_NOUN_PREFIX_RE = re.compile(r"スキル$")


_SKILL_SUFFIX_MARKER_RE = re.compile(r"^スキルを(?:起動|呼び出)")


_DIRECT_INVOCATION_RE = re.compile(r"^を(?:起動|呼び出)")


_AGENT_CALL_RE = re.compile(r"(?:Agentツールで|subagent_type:\s*)`?([A-Za-z0-9:_-]+)`?")


_GENERIC_AGENT_TYPES = frozenset({"claude", "Explore", "Plan"})


def _git_root(work_dir: pathlib.Path) -> tuple[pathlib.Path | None, str | None]:
    """作業ディレクトリが属するGitルートの正規化済みパスを返す。"""
    result = _git_command.run(
        ["-C", str(work_dir), "rev-parse", "--show-toplevel"], capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        return None, result.stderr.strip() or "作業ディレクトリのGitルートを解決できない"
    return pathlib.Path(result.stdout.strip()).resolve(), None


def _check_target_repo(declared_value: str | None, work_dir: pathlib.Path) -> list[str]:
    """宣言された対象リポジトリと作業ディレクトリのGitルートが一致するか確かめる。"""
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
            f"計画メタ情報の対象リポジトリが作業ディレクトリのGitルートと一致しない: 計画={declared_root}, 実際={actual_root}。"
            + _next_action.next_action_line(TARGET_REPO_NEXT_ACTION)
        ]
    return []


def _check_references(text: str, work_dir: pathlib.Path) -> list[str]:
    """コードフェンスを除く本文のスキル・専用agent参照が実在するか確かめる。"""
    inline_text = structure.markdown_body_text(text)
    errors: list[str] = []
    agent_calls = set(_AGENT_CALL_RE.findall(inline_text)) - _GENERIC_AGENT_TYPES
    skill_calls = _classify_skill_references(inline_text) - agent_calls
    for skill in sorted(skill_calls):
        namespace, separator, qualified_name = skill.partition(":")
        if separator and namespace != "agent-toolkit":
            errors.append(_missing_reference_message("スキル", skill, _known_skill_names(work_dir)))
            continue
        name = qualified_name if separator else namespace
        plugin_candidates = (_PLUGIN_DIR / "skills" / name / "SKILL.md",)
        project_candidates = (
            work_dir / ".claude" / "skills" / name / "SKILL.md",
            work_dir / ".agents" / "skills" / name / "SKILL.md",
        )
        candidates = plugin_candidates if separator else plugin_candidates + project_candidates
        if not any(path.exists() for path in candidates):
            errors.append(_missing_reference_message("スキル", skill, _known_skill_names(work_dir)))
    for agent in sorted(agent_calls):
        namespace, separator, qualified_name = agent.partition(":")
        if separator and namespace != "agent-toolkit":
            errors.append(_missing_reference_message("サブエージェント", agent, _known_agent_names(work_dir)))
            continue
        name = qualified_name if separator else namespace
        plugin_candidates = (_PLUGIN_DIR / "agents" / f"{name}.md",)
        project_candidates = (work_dir / ".claude" / "agents" / f"{name}.md",)
        candidates = plugin_candidates if separator else plugin_candidates + project_candidates
        if not any(path.exists() for path in candidates):
            errors.append(_missing_reference_message("サブエージェント", agent, _known_agent_names(work_dir)))
    return errors


def _known_skill_names(work_dir: pathlib.Path) -> list[str]:
    """参照先として実在するスキル名を、プラグイン修飾付きとプロジェクトの素の名前で返す。"""
    names = [f"agent-toolkit:{path.parent.name}" for path in (_PLUGIN_DIR / "skills").glob("*/SKILL.md")]
    for root in (work_dir / ".claude" / "skills", work_dir / ".agents" / "skills"):
        names.extend(path.parent.name for path in root.glob("*/SKILL.md"))
    return sorted(set(names))


def _known_agent_names(work_dir: pathlib.Path) -> list[str]:
    """参照先として実在するサブエージェント名を返す。"""
    names = [f"agent-toolkit:{path.stem}" for path in (_PLUGIN_DIR / "agents").glob("*.md")]
    names.extend(path.stem for path in (work_dir / ".claude" / "agents").glob("*.md"))
    return sorted(set(names))


def _missing_reference_message(kind: str, reference: str, known: list[str]) -> str:
    """実在しない参照の違反文へ、近い名前の候補と直し方を加える。"""
    candidates = difflib.get_close_matches(reference, known, n=3, cutoff=0.6)
    hint = f"候補: {', '.join(candidates)}。" if candidates else ""
    return f"実在しない{kind}参照: {reference}。{hint}実在する{kind}名へ直すか、起動の形の参照をやめる"


def _classify_skill_references(text: str) -> set[str]:
    """起動または呼び出しを指示するスキル参照だけを返す。"""
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

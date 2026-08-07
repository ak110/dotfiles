#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Codex向けAGENTS.mdをベース記述とagent-toolkit rulesから生成する。"""

from __future__ import annotations

import sys
from pathlib import Path

from codex_shared_rules import CODEX_EXCLUDED_RULE_NAMES, is_codex_shared_rule

__all__ = ["CODEX_EXCLUDED_RULE_NAMES", "is_codex_shared_rule"]

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from pytools._internal import claude_common  # pylint: disable=wrong-import-position  # noqa: E402

BASE_SOURCE = Path("scripts/codex-agents-base.md")
RULES_SOURCE = Path("agent-toolkit/rules")
TARGET = Path(".chezmoi-source/dot_codex/AGENTS.md")
PROJECT_AGENTS = Path("AGENTS.md")
MAX_BYTES = 128 * 1024
GENERATED_MARKER = "<!-- 自動生成ファイル。scripts/sync_generated_files.pyで再生成する。手動編集禁止。 -->"


def render(root: Path = REPO_ROOT) -> str:
    """生成内容を決定的に組み立てる。"""
    sections = [GENERATED_MARKER, "", (root / BASE_SOURCE).read_text(encoding="utf-8").rstrip("\n")]
    for rule in sorted(path for path in (root / RULES_SOURCE).glob("*.md") if is_codex_shared_rule(path)):
        relative = rule.relative_to(root).as_posix()
        sections.extend(
            [
                "",
                f"<!-- BEGIN: {relative} -->",
                rule.read_text(encoding="utf-8").rstrip("\n"),
                f"<!-- END: {relative} -->",
            ]
        )
    return "\n".join(sections) + "\n"


def sync(root: Path = REPO_ROOT) -> bool:
    """生成物を冪等同期し、変更した場合はTrueを返す。"""
    content = render(root)
    project_content = (root / PROJECT_AGENTS).read_bytes()
    if len(content.encode()) + len(project_content) > MAX_BYTES:
        raise ValueError(f"Codex instruction chainが{MAX_BYTES} bytesを超える")
    target = root / TARGET
    if target.exists() and target.read_text(encoding="utf-8") == content:
        return False
    if not claude_common.atomic_write_text(target, content, tag="codex agents"):
        raise OSError(f"Codex AGENTS.mdの書き込みに失敗: {TARGET}")
    return True


def main() -> int:
    """Codex向けAGENTS.mdを同期する。"""
    sync()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

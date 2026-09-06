#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["pytilpack[quart]>=1.47.0"]
# ///
"""Codex向けAGENTS.mdをベース記述とagent-toolkit rulesから生成する。"""

from __future__ import annotations

import argparse
import sys
import tomllib
from pathlib import Path

from codex_shared_rules import CODEX_EXCLUDED_RULE_NAMES, is_codex_shared_rule

__all__ = ["CODEX_EXCLUDED_RULE_NAMES", "is_codex_shared_rule"]

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from pytools._internal import claude_common  # pylint: disable=wrong-import-position  # noqa: E402

BASE_SOURCE = Path("agent-toolkit/share/codex-agents-base.md")
PERSONAL_SOURCE = Path(".chezmoi-source/dot_claude/rules/myprojects-common.md")
RULES_SOURCE = Path("agent-toolkit/rules")
TARGET = Path(".chezmoi-source/dot_codex/AGENTS.md")
PROJECT_AGENTS = Path("AGENTS.md")
CODEX_CONFIG = Path("scripts/codex_config.toml")
GENERATED_MARKER = "<!-- 自動生成ファイル。scripts/sync_generated_files.pyで再生成する。手動編集禁止。 -->"


def render(root: Path = REPO_ROOT) -> str:
    """生成内容を決定的に組み立てる。"""
    sections = [GENERATED_MARKER, "", (root / BASE_SOURCE).read_text(encoding="utf-8").rstrip("\n")]
    sections.extend(_embedded_section(root, PERSONAL_SOURCE))
    for rule in sorted(path for path in (root / RULES_SOURCE).glob("*.md") if is_codex_shared_rule(path)):
        sections.extend(_embedded_section(root, rule.relative_to(root)))
    return "\n".join(sections) + "\n"


def _embedded_section(root: Path, relative: Path) -> list[str]:
    """埋め込み元のパスを示すマーカーの間に本文を配置する。"""
    marker = relative.as_posix()
    body = (root / relative).read_text(encoding="utf-8").rstrip("\n")
    return ["", f"<!-- BEGIN: {marker} -->", body, f"<!-- END: {marker} -->"]


def _codex_limits(root: Path) -> tuple[int, float]:
    """指示連結の上限と警告比率を唯一の設定元から読む。

    chezmoiの設定テンプレートも同じ設定元から`project_doc_max_bytes`を読む。
    """
    with (root / CODEX_CONFIG).open("rb") as config_file:
        config = tomllib.load(config_file)
    max_bytes: object = config["project_doc_max_bytes"]
    warn_ratio: object = config["project_doc_warn_ratio"]
    if not isinstance(max_bytes, int) or isinstance(max_bytes, bool):
        raise TypeError("project_doc_max_bytesは整数で指定する")
    if not isinstance(warn_ratio, (int, float)) or isinstance(warn_ratio, bool):
        raise TypeError("project_doc_warn_ratioは数値で指定する")
    if warn_ratio <= 0 or warn_ratio > 1:
        raise ValueError("project_doc_warn_ratioは0より大きく1以下で指定する")
    return max_bytes, float(warn_ratio)


def sync(root: Path = REPO_ROOT) -> bool:
    """生成物を冪等同期し、変更した場合はTrueを返す。"""
    content = render(root)
    project_content = (root / PROJECT_AGENTS).read_bytes()
    max_bytes, warn_ratio = _codex_limits(root)
    total = len(content.encode()) + len(project_content)
    if total > max_bytes:
        raise ValueError(f"Codex instruction chainが{max_bytes} bytesを超える")
    if total >= max_bytes * warn_ratio:
        print(
            f"警告: Codex instruction chainが{total} bytesとなり、上限{max_bytes} bytesの警告比率{warn_ratio:g}へ達した。"
            "規範の総量を減らすか、scripts/codex_config.tomlのproject_doc_max_bytesを引き上げる。",
            file=sys.stderr,
        )
    target = root / TARGET
    if target.exists() and target.read_text(encoding="utf-8") == content:
        return False
    if not claude_common.atomic_write_text(target, content, tag="codex agents"):
        raise OSError(f"Codex AGENTS.mdの書き込みに失敗: {TARGET}")
    return True


def main(argv: list[str] | None = None) -> int:
    """Codex向けAGENTS.mdを同期する。"""
    argparse.ArgumentParser(description="Codex向けAGENTS.mdを同期する。").parse_args(argv)

    sync()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

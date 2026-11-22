#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.15"
# dependencies = ["psutil", "pytilpack[quart]>=1.47.0"]
# ///
"""Codex向けAGENTS.mdを個人用の規範から生成する。

agent-toolkitの規範は入口のスキル`atk-mode`と手順書が持つため、ここへは埋め込まない。
"""

from __future__ import annotations

import argparse
import sys
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from pytools._internal import common  # pylint: disable=wrong-import-position  # noqa: E402

PERSONAL_SOURCE = Path(".chezmoi-source/dot_claude/rules/myprojects-common.md")
TARGET = Path(".chezmoi-source/dot_codex/AGENTS.md")
PROJECT_AGENTS = Path("AGENTS.md")
CODEX_CONFIG = Path("share/codex_config.toml")
GENERATED_MARKER = (
    "<!-- dotfilesリポジトリの.chezmoi-source/dot_codex/AGENTS.mdから~/.codex/AGENTS.mdへ配布する自動生成ファイル。"
    "dotfilesリポジトリのscripts/sync_generated_files.pyで再生成し、手動編集しない。"
    "本文は全リポジトリへ適用する個人用の規範である。 -->"
)

NORMATIVE_ELEMENT = "atk-auto"
NORMATIVE_SOURCE = "dotfiles"
NORMATIVE_KIND = "rules"


def render(root: Path = REPO_ROOT) -> str:
    return "\n".join([GENERATED_MARKER, *_embedded_section(root, PERSONAL_SOURCE)]) + "\n"


def _has_normative_boundary(body: str) -> bool:
    prefix = f"<{NORMATIVE_ELEMENT}"
    if not body.startswith(prefix):
        return False
    rest = body[len(prefix) :]
    return rest.startswith(">") or rest[:1].isspace()


def _embedded_section(root: Path, relative: Path) -> list[str]:
    marker = relative.as_posix()
    body = (root / relative).read_text(encoding="utf-8").rstrip("\n")
    if _has_normative_boundary(body):
        return ["", body]
    opening = f'<{NORMATIVE_ELEMENT} source="{NORMATIVE_SOURCE}" kind="{NORMATIVE_KIND}" path="{marker}">'
    return ["", opening, body, f"</{NORMATIVE_ELEMENT}>"]


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
            "規範の総量を減らすか、share/codex_config.tomlのproject_doc_max_bytesを引き上げる。",
            file=sys.stderr,
        )
    target = root / TARGET
    if target.exists() and target.read_text(encoding="utf-8") == content:
        return False
    if not common.atomic_write_text(target, content, tag="codex agents"):
        raise OSError(
            f"Codex AGENTS.mdの書き込みに失敗: {TARGET}。保存先の書込権限と空き容量を確認して、"
            "uv run --frozen python scripts/sync_codex_agents.pyを再実行してください。"
        )
    return True


def main(argv: list[str] | None = None) -> int:
    """Codex向けAGENTS.mdを同期する。"""
    argparse.ArgumentParser(description="Codex向けAGENTS.mdを同期する。").parse_args(argv)

    sync()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

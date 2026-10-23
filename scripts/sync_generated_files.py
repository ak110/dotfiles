"""リポジトリ内の自動生成ファイルを一括同期する。

起動は`uv run --frozen python scripts/sync_generated_files.py`とする。
本スクリプトは`sys.executable`で各生成器を子プロセス起動するため、子が要求するプロジェクト依存を持つ
プロジェクト環境のPythonで起動する。
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

from pytools._internal.common import atomic_write_text

REPO_ROOT = Path(__file__).resolve().parent.parent
QUALITY_SOURCE = "agent-basics/skills/writing-standards/references/quality-principles.md"
QUALITY_TARGETS = (
    ("agent-toolkit/rules/01-agent.md", "roles", "役割分担", 2),
    ("agent-toolkit/rules/01-agent.md", "qcd", "QCDと3段判定", 3),
    ("agent-toolkit/rules/01-agent.md", "constraints", "規定の区分と標示", 3),
    ("agent-toolkit/rules/01-agent.md", "facts", "事実と推論", 2),
    ("agent-toolkit/share/agents-server-base.md", "result", "結果の返却", 2),
)
GENERATORS = (
    "scripts/sync_codex_plugin_manifests.py",
    "scripts/gen_completions.py",
    "scripts/gen_install_files.py",
    "scripts/sync_codex_agents.py",
    "scripts/sync_antigravity_plugin.py",
)


def sync_quality_principles(root: Path = REPO_ROOT) -> None:
    """basics正本の指定節だけを、常時規範と軽量返却契約の既存配置先へ同期する。

    同梱した本文で判断できるよう生成時に取り込み、配布先ではtoolkit内の本文だけで品質判断を完結させる。
    後続のCodex・Antigravity生成は更新後の本文を使う。
    """
    source = (root / QUALITY_SOURCE).read_text(encoding="utf-8")
    sections: dict[str, tuple[int, str]] = {}
    headings = list(re.finditer(r"^(#{2,}) (.+)$", source, re.MULTILINE))
    for index, heading in enumerate(headings):
        level = len(heading[1])
        end = next((item.start() for item in headings[index + 1 :] if len(item[1]) <= level), len(source))
        sections[heading[2]] = (level, source[heading.end() : end].strip())
    updated: dict[Path, str] = {}
    for relative, key, title, target_level in QUALITY_TARGETS:
        path = root / relative
        text = updated[path] if path in updated else path.read_text(encoding="utf-8")
        level, body = sections[title]

        def shift_heading(match: re.Match[str], shift: int = target_level - level) -> str:
            """配置先の見出し階層へそろえる。"""
            return "#" * (len(match[1]) + shift)

        body = re.sub(
            r"^(#{2,})(?= )",
            shift_heading,
            body,
            flags=re.MULTILINE,
        )
        start = f"<!-- quality-principles:{key}:start -->"
        end = f"<!-- quality-principles:{key}:end -->"
        if text.count(start) != 1 or text.count(end) != 1:
            raise ValueError(f"{relative}: {key}の生成境界が一意でない")
        before, rest = text.split(start)
        _, after = rest.split(end)
        updated[path] = f"{before}{start}\n<!-- 生成元: {QUALITY_SOURCE}「{title}」。手動編集しない。 -->\n{body}\n{end}{after}"
    for path, text in updated.items():
        if path.read_text(encoding="utf-8") != text:
            atomic_write_text(path, text)


def run_generator(path: str) -> int:
    """個別生成器を現在のPythonで実行する。"""
    return subprocess.run([sys.executable, path], cwd=REPO_ROOT, check=False).returncode


def main(argv: list[str] | None = None) -> int:
    """全生成器を固定順で実行し、失敗を集約する。"""
    argparse.ArgumentParser(description=__doc__).parse_args(argv)
    try:
        sync_quality_principles()
    except (OSError, KeyError, ValueError) as error:
        print(f"品質原則の同期に失敗: {error}。次の操作: 正本の節と配置先の生成境界を修正してください。", file=sys.stderr)
        return 1
    failures: list[str] = []
    for generator in GENERATORS:
        if run_generator(generator) != 0:
            failures.append(generator)
    if failures:
        print(
            "生成に失敗: " + ", ".join(failures) + "。次の操作: 上記の生成器の失敗理由を修正し、"
            "uv run --frozen python scripts/sync_generated_files.pyを再実行してください。",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

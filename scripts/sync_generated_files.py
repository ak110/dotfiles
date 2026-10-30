"""リポジトリ内の自動生成ファイルを一括同期する。

起動は`uv run --frozen python scripts/sync_generated_files.py`とする。
本スクリプトは`sys.executable`で各生成器を子プロセス起動するため、子が要求するプロジェクト依存を持つ
プロジェクト環境のPythonで起動する。
"""

from __future__ import annotations

import argparse
import os
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
RETURN_TARGETS = (
    ("exec", "share/exec.parent.md", "レーン担当の返却契約", 2),
    ("exec-review", "share/exec-review.parent.md", "実行レビューの返却契約", 2),
    ("reader-fit-review", "share/reader-fit-review.parent.md", "読者別探索の返却契約", 2),
    ("wi-draft-review", "share/wi-draft-review.parent.md", "投入前レビューの返却契約", 2),
    ("recurrence-self-check", "share/exec-review.subagent.md", "セルフチェックの返却契約", 3),
    ("add-wi", "share/add-wi.parent.md", "WI投入担当の返却契約", 2),
    ("lane-integration", "share/exec.parent.md", "統合の返却契約", 2),
    ("copilot-review-audit", "skills/process-wi/references/review-audit-launch.md", "監査担当の返却契約", 3),
    ("refine-prompt", "skills/refine-prompt/SKILL.md", "評価担当の返却契約", 3),
    ("defect-investigation", "skills/delegation/references/investigation-delegation.md", "調査担当の返却契約", 3),
    ("external-write-review", "skills/external-write-review/SKILL.md", "投稿前レビューの返却契約", 3),
    ("bulk-replace-review", "skills/delegation/references/bulk-replacement.md", "一括置換後レビューの返却契約", 3),
    ("usability-review", "share/usability-review.parent.md", "ユーザビリティレビューの返却契約", 2),
    ("session-termination", "share/session-termination.parent.md", "公開担当の返却契約", 2),
)


def _return_body(source: str, role: str) -> str:
    """フェンス外の見出し境界で出力節全体を抽出する。"""
    lines: list[str] = []
    selected = False
    fence = ""
    found = 0
    for line in source.splitlines():
        marker = re.match(r"^(`{3,}|~{3,})", line)
        if marker:
            if not fence:
                fence = marker[1]
            elif marker[1][0] == fence[0] and len(marker[1]) >= len(fence):
                fence = ""
        if not fence and line == "## 出力":
            selected = True
            found += 1
            continue
        if not fence and re.match(r"^#{1,2} ", line):
            selected = False
        if selected:
            lines.append(line)
    if found != 1:
        raise ValueError(f"{role}: 出力節がない")
    return "\n".join(lines).strip()


def sync_return_contracts(root: Path = REPO_ROOT) -> None:
    """役割の正本から既存の呼出側文書へ返却契約だけを同期する。"""
    plugin = root / "agent-toolkit"
    updated: dict[Path, str] = {}
    for role, relative, title, level in RETURN_TARGETS:
        source = plugin / f"share/{role}.subagent.md"
        path = plugin / relative
        body = _return_body(source.read_text(encoding="utf-8"), role)
        # コード例の見出しは出力リテラルなので、フェンス外だけ階層を変える。
        lines = []
        fenced = False
        for line in body.splitlines():
            if line.startswith("```"):
                fenced = not fenced
            if not fenced and re.match(r"^#{3,} ", line):
                line = "#" * (level - 2) + line
            lines.append(line)
        body = "\n".join(lines)

        def relocate(match: re.Match[str], source: Path = source, path: Path = path) -> str:
            value = match[1]
            reference, separator, heading = value.partition("#")
            if reference.startswith(("/", "${")) or ":" in reference:
                return match[0]
            target = root / reference if reference.startswith("agent-toolkit/") else source.parent / reference
            if reference == "exec-review-recording.md":
                target = plugin / "share/exec-review-recording.md"
            if not target.is_file():
                return match[0]
            return "`" + Path(os.path.relpath(target, path.parent)).as_posix() + separator + heading + "`"

        body = re.sub(r"`([^`\n]+\.md(?:#[^`\n]*)?)`", relocate, body)

        def relocate_link(match: re.Match[str], source: Path = source, path: Path = path) -> str:
            """リンク先だけを相対化し、ラベルと節の指定を保つ。"""
            return relocate_path(match[0], source, path)

        body = re.sub(
            r"(?<=\]\()([^\s()]+\.md(?:#[^\s()]*)?)(?=\))",
            relocate_link,
            body,
        )
        start = f"<!-- return-contract:{role}:start -->"
        end = f"<!-- return-contract:{role}:end -->"
        text = updated.get(path, path.read_text(encoding="utf-8"))
        if text.count(start) != 1 or text.count(end) != 1:
            raise ValueError(f"{relative}: {role}の生成境界が一意でない")
        before, rest = text.split(start)
        _, after = rest.split(end)
        updated[path] = (
            f"{before}{start}\n<!-- 生成元: share/{role}.subagent.md「出力」。手動編集しない。 -->\n"
            f"{'#' * level} {title}\n\n{body}\n{end}{after}"
        )
    for path, text in updated.items():
        if path.read_text(encoding="utf-8") != text:
            atomic_write_text(path, text)


def relocate_path(reference: str, source: Path, target: Path) -> str:
    """Markdownリンクの相対参照を配置先から解決できる形にする。"""
    name, separator, heading = reference.partition("#")
    if name.startswith(("/", "${")) or ":" in name:
        return reference
    return Path(os.path.relpath(source.parent / name, target.parent)).as_posix() + separator + heading


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
        sync_return_contracts()
    except (OSError, KeyError, ValueError) as error:
        print(f"契約本文の同期に失敗: {error}。次の操作: 正本の節と配置先の生成境界を修正してください。", file=sys.stderr)
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

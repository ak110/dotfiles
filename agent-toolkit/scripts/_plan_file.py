"""Plan file（`~/.claude/plans/*.md`）判定の共通ユーティリティ。

pretooluse / posttooluse の双方から参照する。`agent-toolkit/scripts/`配下の
配布物独立性を保つため`pytools/_internal/`は参照せず近接配置する。
"""

import pathlib


def is_plan_file(file_path: str) -> bool:
    """`~/.claude/plans/`直下のplan file（`*.md`）の場合に真を返す。

    `.review.md` / `.codex.log`は副次ファイルのため除外する。
    サブディレクトリ配下のファイルは対象外（直下のみ）。
    """
    if not file_path:
        return False
    try:
        path = pathlib.Path(file_path).resolve()
        plans_dir = (pathlib.Path.home() / ".claude" / "plans").resolve()
        rel = path.relative_to(plans_dir)
    except (OSError, ValueError):
        return False
    if len(rel.parts) != 1:
        return False
    name = rel.parts[0]
    if name.endswith(".review.md") or name.endswith(".codex.log"):
        return False
    return name.endswith(".md")

"""Plan file（`~/.claude/plans/*.md`）判定の共通ユーティリティ。

pretooluse / posttooluse の双方から参照する。`agent-toolkit/scripts/`配下の
配布物独立性を保つため`pytools/_internal/`は参照せず近接配置する。

計画は`<計画名>.md`（計画本体・人間/メイン向け）と`<計画名>.detail.md`
（実装詳細・実装者向け）の2ファイル構成を取り得る。用途により判定対象が
異なるため、計画本体だけを真とする`is_plan_main_file`と、両ファイルを
真とする`is_plan_component_file`、バグ調査付属ファイルを真とする
`is_plan_adjunct_file`の3述語を提供する。`.review.md`等の副次ファイルは
計画構成要素ではない。
"""

import pathlib


def _plan_file_name(file_path: str) -> str | None:
    """`~/.claude/plans/`直下のファイル名を返す。対象外の場合はNoneを返す。

    サブディレクトリ配下のファイルは対象外（直下のみ）。
    """
    if not file_path:
        return None
    try:
        path = pathlib.Path(file_path).resolve()
        plans_dir = (pathlib.Path.home() / ".claude" / "plans").resolve()
        rel = path.relative_to(plans_dir)
    except (OSError, ValueError):
        return None
    if len(rel.parts) != 1:
        return None
    return rel.parts[0]


def _is_component_name(name: str) -> bool:
    """計画構成要素（計画本体または実装詳細）のファイル名かを判定する。"""
    if (
        name.endswith(".review.md")
        or name.endswith(".codex.log")
        or name.endswith("-workaround-check.md")
        or name.endswith(".bugs.md")
    ):
        return False
    return name.endswith(".md")


def is_plan_component_file(file_path: str) -> bool:
    """計画構成要素（計画本体`.md`又は実装詳細`.detail.md`）の場合に真を返す。

    `.review.md` / `.codex.log` / `-workaround-check.md` / `.bugs.md`は副次ファイルのため除外する。
    """
    name = _plan_file_name(file_path)
    return name is not None and _is_component_name(name)


def is_plan_main_file(file_path: str) -> bool:
    """計画本体（メイン側`.md`）の場合に真を返す。実装詳細側`.detail.md`は偽。"""
    name = _plan_file_name(file_path)
    if name is None or not _is_component_name(name):
        return False
    return not name.endswith(".detail.md")


def is_plan_adjunct_file(file_path: str) -> bool:
    """計画付属のバグ調査ファイル（`~/.claude/plans/`直下の`.bugs.md`）の場合に真を返す。"""
    name = _plan_file_name(file_path)
    return name is not None and name.endswith(".bugs.md")

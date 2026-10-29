"""規範Markdownの容量が全文取得の閾値に収まることを確かめる。"""

import pathlib

from agent_toolkit._hooks.pretooluse.codex_read_capture import standard_read

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]


def test_agent_toolkit_markdown_fits_full_read_threshold() -> None:
    """全文取得を求める規範Markdownを、通常のnative全量配送が識別できる。

    実容量は実行時のモデル文脈と同時配送の合計で判定する。
    対象はagent-toolkitの`skills`・`share`・`rules`とagent-basicsの`skills`である。
    全文取得を求める規範の配布元を新しく加える場合は、この対象も見直す。
    """
    roots = [_REPO_ROOT / "agent-toolkit" / name for name in ("skills", "share", "rules")]
    roots.append(_REPO_ROOT / "agent-basics" / "skills")
    paths = [path for root in roots for path in sorted(root.rglob("*.md"))]
    assert paths
    oversized = [
        f"{path.relative_to(_REPO_ROOT)}: {path.stat().st_size}バイト" for path in paths if not standard_read(f"cat -- {path}")
    ]
    assert not oversized, f"Codexの全文配送で識別できない: {oversized}"

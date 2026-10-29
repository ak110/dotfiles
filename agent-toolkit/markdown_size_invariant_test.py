"""規範Markdownが標準読取の自動配送か従来の容量内で全文取得できることを確かめる。"""

import pathlib

import pytest

from agent_toolkit._hooks.pretooluse.large_reads import check_large_bash_read

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]


def _codex_read(command: str, cwd: pathlib.Path) -> str | None:
    return check_large_bash_read(command, str(cwd), is_codex=True)


def test_agent_toolkit_markdown_fits_full_read_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    """全文取得を求める規範Markdownは、自動配送または従来の容量内で読めるようにする。

    自動配送の対象外で閾値を超える文書は、`references/`への分割で閾値以下へ戻す。
    対象はagent-toolkitの`skills`・`share`・`rules`とagent-basicsの`skills`である。
    全文取得を求める規範の配布元を新しく加える場合は、この対象も見直す。
    """
    monkeypatch.delenv("AGENT_TOOLKIT_LARGE_READ_BYTES", raising=False)
    roots = [_REPO_ROOT / "agent-toolkit" / name for name in ("skills", "share", "rules")]
    roots.append(_REPO_ROOT / "agent-basics" / "skills")
    paths = [path for root in roots for path in sorted(root.rglob("*.md"))]
    assert paths
    oversized = [
        f"{path.relative_to(_REPO_ROOT)}: {path.stat().st_size}バイト"
        for path in paths
        if _codex_read(f"cat {path}", _REPO_ROOT) is not None
    ]
    assert not oversized, f"Codexの全文取得の閾値を超える: {oversized}"

"""規範Markdownの容量が全文取得の閾値に収まることを確かめる。"""

import pathlib

import pytest

from agent_toolkit._hooks.pretooluse.large_reads import check_large_bash_read

pytestmark = pytest.mark.repo_invariant

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]


def _codex_read(command: str, cwd: pathlib.Path) -> str | None:
    return check_large_bash_read(command, str(cwd), is_codex=True)


@pytest.mark.repo_invariant
def test_agent_toolkit_markdown_fits_full_read_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    """agent-toolkitの規範Markdownは、Codexが遮断されずに1回の全文取得で読める容量に収める。

    閾値を超える文書は読むたびに遮断と分割取得を要するため、`references/`への分割で閾値以下へ戻す。
    """
    monkeypatch.delenv("AGENT_TOOLKIT_LARGE_READ_BYTES", raising=False)
    roots = [_REPO_ROOT / "agent-toolkit" / name for name in ("skills", "share", "rules")]
    paths = [path for root in roots for path in sorted(root.rglob("*.md"))]
    assert paths
    oversized = [
        f"{path.relative_to(_REPO_ROOT)}: {path.stat().st_size}バイト"
        for path in paths
        if _codex_read(f"cat {path}", _REPO_ROOT) is not None
    ]
    assert not oversized, f"Codexの全文取得の閾値を超える: {oversized}"

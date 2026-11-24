import pytest

from agent_toolkit._common import prevention_kinds


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("自動検出: 検査を足す。文書: 実例を載せる。", True),
        ("被覆 既存の検査が防ぐ。", True),
        ("**見送り**: 継続コストが大きい。", True),
        ("引き継ぎ。観測: …", True),
        ("設計書を直す。", False),
        ("文書化する。", False),
        ("担当が注意する。", False),
    ],
)
def test_starts_with_kind(text: str, *, expected: bool) -> None:
    assert prevention_kinds.starts_with_kind(text) is expected

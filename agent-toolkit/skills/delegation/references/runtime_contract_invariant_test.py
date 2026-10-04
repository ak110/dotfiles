"""定期再確認の標識と実行手順の一致を確かめる。"""

import pathlib

from agent_toolkit._hooks import user_prompt_submit


def test_periodic_recheck_marker_matches_the_runtime_document() -> None:
    """フックの標識と`claude-code-runtime.md`の記述が同じリテラルを持つ。

    標識を2箇所が保持するため、片方だけの改訂で機械注入判定が成立しなくなる状態を検出する。
    """
    document = pathlib.Path(__file__).resolve().with_name("claude-code-runtime.md")
    assert f"`{user_prompt_submit.PERIODIC_RECHECK_MARKER}`" in document.read_text(encoding="utf-8")

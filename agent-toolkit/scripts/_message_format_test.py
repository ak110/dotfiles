"""コーディングエージェント宛て共有メッセージ整形を検証する。"""

import pytest
from _message_format import llm_notice


@pytest.mark.parametrize(
    ("tag", "expected_prefix"),
    [
        pytest.param("", "[auto-generated: agent-toolkit/example]", id="default"),
        pytest.param("warn", "[auto-generated: agent-toolkit/example][warn]", id="tagged"),
    ],
)
def test_llm_notice_wraps_body_with_standard_markers(tag: str, expected_prefix: str) -> None:
    """タグ有無にかかわらず共通プレフィックス・本文・サフィックスを保つ。"""
    assert llm_notice("本文", "agent-toolkit/example", tag=tag) == (
        f"{expected_prefix} 本文 "
        "(Auto-generated hook notice; evaluate relevance against the conversation context before acting.)"
    )

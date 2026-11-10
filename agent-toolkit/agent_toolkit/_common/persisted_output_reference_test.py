"""退避案内と、その本文に引用された案内の境界を検証する。"""

import pytest

from agent_toolkit._common import message_format, persisted_output_reference


@pytest.mark.parametrize("case", ["ordinary", "claude", "quoted-claude"])
def test_reference_ignores_quoted_notice_and_preview(tmp_path, case):
    """保存案内の先頭から保存先を取得し、通常通知やPreviewの引用と区別する。"""
    quoted = tmp_path / "quoted.txt"
    actual = tmp_path / "actual.txt"
    quote = f"{persisted_output_reference.CODEX_NOTICE_REFERENCE_PREFIX}{quoted}"
    claude = (
        f"{persisted_output_reference.PERSISTED_OUTPUT_PREFIX}\n"
        f"Output too large (8KB). Full output saved to: {actual}\n\nPreview:\n{quote}\n</persisted-output>"
    )
    body = (
        claude
        if case == "claude"
        else message_format.auto_message(
            "入力の引用:\n```text\n" + (quote if case == "ordinary" else claude) + "\n```",
            source="pretooluse",
            kind="warn",
        )
    )
    assert persisted_output_reference.reference_path(body) == (actual if case == "claude" else None)

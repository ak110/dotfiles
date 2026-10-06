import pytest

from agent_toolkit._common.runtime_identity import (
    RuntimeIdentity,
    co_author_trailer,
    distinct_identities,
    latest_identity,
)


def test_runtime_identity_keeps_source_and_machine_value() -> None:
    identity = RuntimeIdentity("codex", "gpt-6.1-sol", "high", "observed")

    assert identity.machine == "codex:gpt-6.1-sol/high"
    assert identity.public()["source"] == "observed"
    assert identity.display() == "GPT-6.1 Sol / High"


def test_runtime_identity_prefers_catalog_display_name() -> None:
    identity = RuntimeIdentity("codex", "gpt-6.1-sol", "xhigh", "observed")

    assert identity.display(catalog=[{"model": "gpt-6.1-sol", "displayName": "Codex Workhorse"}]) == ("Codex Workhorse / Xhigh")


def test_co_author_trailer_rejects_launch_candidate() -> None:
    identity = RuntimeIdentity("codex", "gpt-6.1-sol", "high", "launch_candidate")

    with pytest.raises(ValueError, match="観測"):
        co_author_trailer(identity)


@pytest.mark.parametrize(
    ("model", "expected"),
    [
        ("claude-opus-5-5", "Co-Authored-By: Claude Opus 5.5 / Medium <noreply@anthropic.com>"),
        ("claude-haiku-4-5-20251001", "Co-Authored-By: Claude Haiku 4.5 / Medium <noreply@anthropic.com>"),
        ("claude-opus-5-5[1m]", "Co-Authored-By: claude-opus-5-5[1m] / Medium <noreply@anthropic.com>"),
    ],
)
def test_co_author_trailer_uses_requested_claude_display_form(model: str, expected: str) -> None:
    """Claudeの帰属行はユーザーが示した表示例（`Claude Opus 5.5 / High`）の形にし、形の合わないIDはそのまま使う。"""
    assert co_author_trailer(RuntimeIdentity("claude", model, "medium", "observed")) == expected


def _claude_line(model: str, effort: str | None) -> dict:
    """Claude Code 2.1.291の記録のassistant行（`message.model`と最上位の`effort`）を返す。"""
    return {"type": "assistant", "version": "2.1.291", "effort": effort, "message": {"model": model, "content": []}}


def _codex_turn_context(model: str, effort: str) -> dict:
    """Codex 0.160.1の記録の`turn_context`行を返す。最上位の`type`が`turn_context`で、`payload`は`type`を持たない。"""
    return {"type": "turn_context", "payload": {"cwd": "/work", "model": model, "effort": effort, "summary": "auto"}}


def test_claude_record_observations_pair_message_model_with_top_level_effort() -> None:
    """Claude Codeの記録から組を取り、合成した応答の行（`<synthetic>`、`effort`がnull）を除く。"""
    records = [
        {"type": "user", "message": {"content": "依頼"}},
        _claude_line("claude-opus-5-5", "medium"),
        _claude_line("<synthetic>", None),
        _claude_line("claude-sonnet-5-5", "high"),
    ]

    assert [item.machine for item in distinct_identities(records, "claude")] == [
        "claude:claude-opus-5-5/medium",
        "claude:claude-sonnet-5-5/high",
    ]


def test_codex_record_observations_keep_all_pairs_and_latest_before_line() -> None:
    records = [
        _codex_turn_context("gpt-6-sol", "high"),
        _codex_turn_context("gpt-6-sol", "high"),
        _codex_turn_context("gpt-6-astra", "xhigh"),
        {"type": "event_msg", "payload": {"type": "token_count"}},
    ]

    assert [item.machine for item in distinct_identities(records, "codex")] == [
        "codex:gpt-6-sol/high",
        "codex:gpt-6-astra/xhigh",
    ]
    latest = latest_identity(records, "codex", before_line=2)
    assert latest is not None and latest[0] == 2 and latest[1].model == "gpt-6-sol"

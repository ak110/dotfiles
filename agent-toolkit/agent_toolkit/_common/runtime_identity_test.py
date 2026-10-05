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


def test_co_author_trailer_keeps_claude_host_values() -> None:
    identity = RuntimeIdentity("claude", "Claude Opus 4.1", "HIGH", "observed")

    assert co_author_trailer(identity) == "Co-Authored-By: Claude Opus 4.1 / HIGH <noreply@anthropic.com>"


def test_codex_record_observations_keep_all_pairs_and_latest_before_line() -> None:
    records = [
        {"type": "event_msg", "payload": {"type": "turn_context", "model": "gpt-6-sol", "effort": "high"}},
        {"type": "event_msg", "payload": {"type": "turn_context", "model": "gpt-6-sol", "effort": "high"}},
        {"type": "event_msg", "payload": {"type": "turn_context", "model": "gpt-6-astra", "effort": "xhigh"}},
    ]

    assert [item.machine for item in distinct_identities(records, "codex")] == [
        "codex:gpt-6-sol/high",
        "codex:gpt-6-astra/xhigh",
    ]
    latest = latest_identity(records, "codex", before_line=2)
    assert latest is not None and latest[0] == 2 and latest[1].model == "gpt-6-sol"

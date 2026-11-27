import json

import pytest

from agent_toolkit._common import env_values


def test_public_environment_keeps_nonsecret_values_and_restores_only_existing_secrets():
    original = "ANTHROPIC_API_KEY=fixture-api\nANTHROPIC_AUTH_TOKEN=fixture-auth\nAWS_SECRET_ACCESS_KEY=fixture-aws\nAWS_SESSION_TOKEN=fixture-session\nX_BEARER_TOKEN=fixture-x\nMY_PASSWORD=fixture-password\nANTHROPIC_BASE_URL=https://example.invalid\nAWS_REGION=us-east-1\nANTHROPIC_MODEL=example-model"
    public = env_values.public("claude_payg_env", original)
    assert "fixture-" not in public
    assert "https://example.invalid" in public and "us-east-1" in public and "example-model" in public
    changed = public.replace("AWS_REGION=us-east-1", "AWS_REGION=eu-west-1")
    assert env_values.parse(env_values.restore("claude_payg_env", changed, original)) == (
        env_values.parse(original) | {"AWS_REGION": "eu-west-1"}
    )
    assert env_values.restore("claude_payg_env", "ANTHROPIC_API_KEY=replaced", original) == "ANTHROPIC_API_KEY=replaced"
    assert env_values.restore("claude_payg_env", "", original) == ""
    with pytest.raises(ValueError, match="保持する値がない"):
        env_values.restore("claude_payg_env", "NEW_TOKEN=[REDACTED]", original)


@pytest.mark.parametrize("text", ["export A=' value '\n# comment\nB=one=two", 'A="quote\'s"\nB='])
def test_environment_roundtrip_preserves_file_semantics(text):
    parsed = env_values.parse(text)
    assert env_values.parse(env_values.encode(parsed)) == parsed


@pytest.mark.parametrize("text", ["BAD-NAME=value", "MISSING_SEPARATOR", "KEY='unclosed", "KEY=bad\0value"])
def test_invalid_environment_never_discloses_values(text):
    with pytest.raises(ValueError) as failure:
        env_values.parse(text)
    assert text not in str(failure.value)
    assert env_values.public("claude_payg_env", text) == "[設定値の書式が不正]"


def test_reference_reads_shared_json_without_a_materialized_env_file(tmp_path):
    path = tmp_path / "atk-config.json"
    path.write_text(json.dumps({"claude_payg_env": "ANTHROPIC_API_KEY=fixture"}), encoding="utf-8")
    assert env_values.read(env_values.reference(path, "claude_payg_env")) == {"ANTHROPIC_API_KEY": "fixture"}

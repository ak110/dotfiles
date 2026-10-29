"""接続選択とsettings合成が同じ環境隔離契約を使うことを確かめる。"""

import json
import pathlib

import pytest

from agent_toolkit._common import claude_connection


@pytest.mark.parametrize("kind", ["payg", "subscription", "inherit"])
def test_selected_connection_owns_every_injected_key(tmp_path: pathlib.Path, kind: str) -> None:
    """任意の追加キーと親settings由来の値も、接続の選択に従って隔離する。"""
    api = tmp_path / "api.env"
    api.write_text(
        "ANTHROPIC_API_KEY=selected-key\nAWS_PROFILE=selected-profile\n"
        "ANTHROPIC_DEFAULT_SONNET_MODEL=api-model\nEXTRA_CONNECTION_OPTION=selected-option\n",
        encoding="utf-8",
    )
    connection = claude_connection.Connection(kind, str(api) if kind == "payg" else None, str(api))
    parent = {
        "ANTHROPIC_API_KEY": "parent-key",
        "AWS_PROFILE": "parent-profile",
        "ANTHROPIC_DEFAULT_SONNET_MODEL": "parent-model",
        "EXTRA_CONNECTION_OPTION": "parent-option",
        "CLAUDE_CODE_OAUTH_TOKEN": "oauth",
        "UNRELATED": "kept",
    }
    environment = claude_connection.connection_environment(parent, connection)
    overrides = claude_connection.connection_overrides(connection)
    merged = json.loads(
        claude_connection.merge_settings(
            json.dumps({"env": {**parent, "EXTRA_CONNECTION_OPTION": "settings-option"}}),
            overrides,
            None,
            isolate_auth=kind != "inherit",
            isolation_keys=claude_connection.isolation_keys(connection),
            cwd=tmp_path,
        )
    )["env"]
    assert environment["UNRELATED"] == merged["UNRELATED"] == "kept"
    if kind == "inherit":
        assert environment == parent
        assert merged["EXTRA_CONNECTION_OPTION"] == "settings-option"
        return
    expected = claude_connection.read_api_env_file(str(api)) if kind == "payg" else {}
    for key in ("ANTHROPIC_API_KEY", "AWS_PROFILE", "ANTHROPIC_DEFAULT_SONNET_MODEL", "EXTRA_CONNECTION_OPTION"):
        assert environment.get(key) == expected.get(key)
        assert merged.get(key) == expected.get(key)
    if kind == "payg":
        assert "CLAUDE_CODE_OAUTH_TOKEN" not in environment
        assert "CLAUDE_CODE_OAUTH_TOKEN" not in merged
    else:
        assert environment["CLAUDE_CODE_OAUTH_TOKEN"] == merged["CLAUDE_CODE_OAUTH_TOKEN"] == "oauth"


def test_restarted_api_reads_changed_file(tmp_path: pathlib.Path) -> None:
    """同じ接続でも、再開の環境は保存したパスから最新値を読む。"""
    path = tmp_path / "api.env"
    path.write_text("CUSTOM_CONNECTION=first\n", encoding="utf-8")
    connection = claude_connection.Connection("payg", str(path))
    assert claude_connection.connection_environment({}, connection)["CUSTOM_CONNECTION"] == "first"
    path.write_text("CUSTOM_CONNECTION=second\n", encoding="utf-8")
    assert claude_connection.connection_environment({}, connection)["CUSTOM_CONNECTION"] == "second"

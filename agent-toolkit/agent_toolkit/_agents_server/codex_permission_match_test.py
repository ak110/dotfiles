import datetime
from pathlib import Path

from agent_toolkit._agents_server.codex_auto_reset import has_permission
from agent_toolkit._atk.usage_refresh import codex_account, codex_permission_account


def test_workspace_routing_is_not_the_codex_permission_account(tmp_path: Path) -> None:
    account = {"type": "chatgpt", "email": "synthetic@example.invalid"}
    auth = {"account": account, "workspaceRouting": {"chatgptAccountId": "synthetic-workspace"}}
    now = datetime.datetime.now(datetime.UTC)
    row = {
        "id": "synthetic-permission",
        "engine": "codex",
        "scope": "codex",
        "count": 1,
        "from": (now - datetime.timedelta(days=1)).isoformat(),
        "until": (now + datetime.timedelta(days=1)).isoformat(),
    }
    assert codex_account(auth) == "synthetic-workspace"
    assert not has_permission([dict(row, account=codex_account(auth))], account, None, state_dir=tmp_path)
    assert has_permission([dict(row, account=codex_permission_account(auth))], account, None, state_dir=tmp_path)

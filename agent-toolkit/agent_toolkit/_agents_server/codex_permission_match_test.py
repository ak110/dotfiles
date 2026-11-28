from agent_toolkit._agents_server.codex_auto_reset import eligible_account
from agent_toolkit._atk.usage_refresh import codex_account, codex_permission_account


def test_workspace_routing_is_not_the_codex_reset_account() -> None:
    account = {"type": "chatgpt", "email": "synthetic@example.invalid"}
    auth = {"account": account, "workspaceRouting": {"chatgptAccountId": "synthetic-workspace"}}
    assert codex_account(auth) == "synthetic-workspace"
    assert codex_permission_account(auth) != codex_account(auth)
    assert not eligible_account({"type": "chatgpt", "accountId": "synthetic-workspace"})
    assert eligible_account(account)

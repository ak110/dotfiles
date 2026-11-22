"""`_hook_agent_id`モジュールのテスト。"""

from agent_toolkit._hooks import agent_id as _hook_agent_id


class TestResolveHookAgentId:
    def test_subagent_payload_returns_agent_id(self):
        assert _hook_agent_id.resolve_hook_agent_id({"agent_id": "abc123"}) == "abc123"

    def test_main_payload_returns_main(self):
        assert _hook_agent_id.resolve_hook_agent_id({"session_id": "s1"}) == "main"

    def test_transcript_path_is_not_used(self):
        payload = {"transcript_path": "/path/to/agent-abc123.jsonl"}
        assert _hook_agent_id.resolve_hook_agent_id(payload) == "main"

    def test_invalid_agent_id_returns_main(self):
        assert _hook_agent_id.resolve_hook_agent_id({"agent_id": ""}) == "main"
        assert _hook_agent_id.resolve_hook_agent_id({"agent_id": 123}) == "main"

    def test_non_dict_payload_returns_main(self):
        assert _hook_agent_id.resolve_hook_agent_id(None) == "main"
        assert _hook_agent_id.resolve_hook_agent_id("payload") == "main"


class TestIsMainAgentContext:
    def test_main_without_delegation_marks_is_true(self):
        assert _hook_agent_id.is_main_agent_context({"session_id": "s1"}, {}) is True

    def test_in_process_subagent_is_false(self):
        assert _hook_agent_id.is_main_agent_context({"agent_id": "abc123"}, {}) is False

    def test_delegated_session_marks_are_false(self):
        payload = {"session_id": "s1"}
        assert _hook_agent_id.is_main_agent_context(payload, {"AGENT_TOOLKIT_DELEGATED_SESSION": "1"}) is False
        assert _hook_agent_id.is_main_agent_context(payload, {"AGENT_TOOLKIT_OWNER_SESSION": "owner-1"}) is False

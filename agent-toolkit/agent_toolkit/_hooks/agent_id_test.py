"""`_hook_agent_id`モジュールのテスト。"""

from agent_toolkit._hooks import agent_id as _hook_agent_id


class TestResolveHookAgentId:
    """`resolve_hook_agent_id`: hook payloadからの呼出主体解決。"""

    def test_subagent_payload_returns_agent_id(self):
        """`agent_id`を持つサブエージェントの呼び出しは当該値を返す。"""
        assert _hook_agent_id.resolve_hook_agent_id({"agent_id": "abc123"}) == "abc123"

    def test_main_payload_returns_main(self):
        """`agent_id`が無いメイン会話の呼び出しは`main`を返す。"""
        assert _hook_agent_id.resolve_hook_agent_id({"session_id": "s1"}) == "main"

    def test_transcript_path_is_not_used(self):
        """サブエージェント名を含む`transcript_path`だけでは呼出主体を判別しない。"""
        payload = {"transcript_path": "/path/to/agent-abc123.jsonl"}
        assert _hook_agent_id.resolve_hook_agent_id(payload) == "main"

    def test_invalid_agent_id_returns_main(self):
        """空文字列・非文字列の`agent_id`は`main`へ倒す。"""
        assert _hook_agent_id.resolve_hook_agent_id({"agent_id": ""}) == "main"
        assert _hook_agent_id.resolve_hook_agent_id({"agent_id": 123}) == "main"

    def test_non_dict_payload_returns_main(self):
        """dict以外のpayloadは`main`を返す。"""
        assert _hook_agent_id.resolve_hook_agent_id(None) == "main"
        assert _hook_agent_id.resolve_hook_agent_id("payload") == "main"


class TestIsMainAgentContext:
    """`is_main_agent_context`: メインだけが処置できる通知の発火条件。"""

    def test_main_without_delegation_marks_is_true(self):
        """`agent_id`も委譲先セッションの印も無い呼び出しだけを真とする。"""
        assert _hook_agent_id.is_main_agent_context({"session_id": "s1"}, {}) is True

    def test_in_process_subagent_is_false(self):
        """`agent_id`を持つin-processのサブエージェントを除く。"""
        assert _hook_agent_id.is_main_agent_context({"agent_id": "abc123"}, {}) is False

    def test_delegated_session_marks_are_false(self):
        """`agents_server`が起動した委譲先セッションの印を持つ実行環境を除く。"""
        payload = {"session_id": "s1"}
        assert _hook_agent_id.is_main_agent_context(payload, {"AGENT_TOOLKIT_DELEGATED_SESSION": "1"}) is False
        assert _hook_agent_id.is_main_agent_context(payload, {"AGENT_TOOLKIT_OWNER_SESSION": "owner-1"}) is False

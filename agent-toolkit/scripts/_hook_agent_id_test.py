"""`_hook_agent_id`モジュールのテスト。"""

import _hook_agent_id


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

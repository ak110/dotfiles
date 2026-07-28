"""`_transcript_agent_id`モジュールのテスト。"""

import _transcript_agent_id


class TestExtractTranscriptAgentId:
    """`extract_transcript_agent_id`: `transcript_path`からのagentId抽出。"""

    def test_valid_agent_id_format(self):
        """正規のagent-<id>.jsonl形式から抽出成功。"""
        result = _transcript_agent_id.extract_transcript_agent_id("agent-abc123.jsonl")
        assert result == "abc123"

    def test_full_path_with_agent_format(self):
        """パスを含む完全なファイル名からもagentId抽出成功。"""
        result = _transcript_agent_id.extract_transcript_agent_id("/path/to/agent-xyz789.jsonl")
        assert result == "xyz789"

    def test_partial_match_not_extracted(self):
        """ファイル名先頭に一致しない場合はNoneを返す。"""
        result = _transcript_agent_id.extract_transcript_agent_id("not-agent-alpha.jsonl")
        assert result is None

    def test_non_string_returns_none(self):
        """非文字列型入力でNoneを返す。"""
        assert _transcript_agent_id.extract_transcript_agent_id(123) is None
        assert _transcript_agent_id.extract_transcript_agent_id(None) is None
        assert _transcript_agent_id.extract_transcript_agent_id(["list"]) is None

    def test_empty_string_returns_none(self):
        """空文字列でNoneを返す。"""
        result = _transcript_agent_id.extract_transcript_agent_id("")
        assert result is None

    def test_wrong_extension_returns_none(self):
        """拡張子が異なる場合はNoneを返す。"""
        result = _transcript_agent_id.extract_transcript_agent_id("agent-abc123.txt")
        assert result is None

    def test_hyphen_in_id(self):
        """agentId自体にハイフンが含まれる場合も抽出成功。"""
        result = _transcript_agent_id.extract_transcript_agent_id("agent-abc-123.jsonl")
        assert result == "abc-123"

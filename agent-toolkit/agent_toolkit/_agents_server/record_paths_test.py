"""record_pathsモジュールのテスト。"""

from agent_toolkit import agents_server_mcp
from agent_toolkit._agents_server import record_paths


def test_record_engines_match_supported_engines() -> None:
    """記録を探索できる実行系は、agents_serverが起動できる実行系と一致する。

    実行系を追加して記録の探索を追随させないと、その実行系の委譲先は`atk agents logs`と
    証拠抽出器の双方で記録なしとして扱われる。
    """
    assert record_paths.RECORD_ENGINES == agents_server_mcp.SUPPORTED_ENGINES

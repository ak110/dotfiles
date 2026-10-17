"""Claude接続の共有settings関数を既存の呼び出し元へ公開する。"""

from agent_toolkit._common import claude_connection

SettingsFile = claude_connection.SettingsFile
api_key_helper = claude_connection.api_key_helper
cleanup_settings = claude_connection.cleanup_settings
merge_settings = claude_connection.merge_settings
read_api_env_file = claude_connection.read_api_env_file

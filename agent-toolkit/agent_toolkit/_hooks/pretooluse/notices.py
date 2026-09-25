# ruff: noqa: F401,F821,I001
# pylint: disable=unused-import,used-before-assignment,wrong-import-order
r"""PreToolUse統合フックが共有する通知整形関数。"""

from __future__ import annotations

from agent_toolkit._hooks.notice import block_formatter as _block_notice_formatter
from agent_toolkit._hooks.notice import formatter as _notice_formatter

# このスクリプトの hook 識別子。
_HOOK_ID = "agent-toolkit/pretooluse"

_llm_notice = _notice_formatter(_HOOK_ID)
_block_notice = _block_notice_formatter(_HOOK_ID)

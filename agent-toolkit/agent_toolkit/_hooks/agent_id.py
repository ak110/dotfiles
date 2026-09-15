"""Claude Code agent-toolkit: hook payloadからの呼出主体（agentId）解決ヘルパー。

`posttooluse.py`・`agents_server_session_advisor.py`・`_uwi_completion.py`が
メイン会話とサブエージェントの呼び出しを区別するために共有する。

判別には`agent_id`だけを用いる。`transcript_path`はサブエージェント内で発火した
`PreToolUse`・`PostToolUse`でもセッション本体の記録を指すため、呼出主体を判別できない
（2026年9月2日、Claude Code公式ドキュメント<https://code.claude.com/docs/en/hooks.md>の
`Common input fields`節で、`agent_id`が「Present only when the hook fires inside a
subagent call. Use this to distinguish subagent hook calls from main-thread calls.」と
定義されていることを確認した。再検証は同節を読む）。
"""

from __future__ import annotations

import os
from collections.abc import Mapping

from agent_toolkit._common.delegated_session import is_delegated

MAIN_AGENT_ID = "main"
"""`agent_id`を持たないメイン会話の呼び出しへ与える識別子。"""


def resolve_hook_agent_id(payload: object) -> str:
    """呼出主体を返す。payloadの`agent_id`があればその値、無ければメイン会話として`main`を返す。"""
    if isinstance(payload, dict):
        agent_id = payload.get("agent_id")
        if isinstance(agent_id, str) and agent_id:
            return agent_id
    return MAIN_AGENT_ID


def is_main_agent_context(payload: object, environ: Mapping[str, str] | None = None) -> bool:
    """呼出主体が最上位セッションのメインであるかを返す。

    in-processのサブエージェントはpayloadの`agent_id`で、`agents_server`が起動した
    委譲先セッションは環境変数の印で除く。
    通知本文が指示する処置をメインだけが実行できる場合に、当該通知の発火条件として使う。
    処置できない主体が通知を受領すると、差し戻しだけの工程が生じる。
    """
    return resolve_hook_agent_id(payload) == MAIN_AGENT_ID and not is_delegated(os.environ if environ is None else environ)

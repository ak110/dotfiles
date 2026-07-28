"""Claude Code agent-toolkit: `transcript_path`からのagentId抽出ヘルパー。

`pretooluse.py`・`posttooluse.py`・`subagent_stop_advisor.py`が同一の抽出ロジックを
共有するために集約する（元は`pretooluse.py`・`subagent_stop_advisor.py`へ重複定義されていた）。
"""

from __future__ import annotations

import pathlib
import re

# `transcript_path`のファイル名（`agent-<agentId>.jsonl`）から`agentId`を抽出する。
_TRANSCRIPT_AGENT_ID_RE = re.compile(r"^agent-([^/\\]+)\.jsonl$")


def extract_transcript_agent_id(transcript_path: object) -> str | None:
    """`transcript_path`のファイル名から`agentId`（`agent-<id>.jsonl`のid部分）を抽出する。

    ファイル名の先頭からの一致のみを許可し、`not-agent-alpha.jsonl`のような
    文字列中の部分一致による誤抽出を防ぐ。抽出できない場合は`None`を返す。
    """
    if not isinstance(transcript_path, str) or not transcript_path:
        return None
    match = _TRANSCRIPT_AGENT_ID_RE.match(pathlib.PurePath(transcript_path).name)
    return match.group(1) if match else None

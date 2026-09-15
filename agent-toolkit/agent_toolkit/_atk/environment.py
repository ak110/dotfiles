"""`atk`を起動した実行環境がコーディングエージェントかどうかの判定。

`atk wi list`の既定出力形式、`atk agents`の出力の整形、及びUWI回答・ユーザーコメントの
書き込み拒否が同じ判定を使うため、特定のサブコマンドの配下ではなく`_atk`直下へ置く。
"""

import os
from collections.abc import Mapping

AGENT_ENVIRONMENT_VARIABLES = ("AI_AGENT", "CODEX_CI", "CLAUDECODE", "CURSOR_AGENT")
"""コーディングエージェントの実行環境を示す環境変数。

いずれか1つでも設定されていればエージェント環境とみなす。
"""


def is_agent_environment(environment: Mapping[str, str] | None = None) -> bool:
    """コーディングエージェントの実行環境から起動されたかを返す。"""
    values = os.environ if environment is None else environment
    return any(name in values for name in AGENT_ENVIRONMENT_VARIABLES)

"""`atk`を起動した実行環境がコーディングエージェントかどうかの判定。

`atk wi list`で形式を指定しない場合の出力、`atk agents`の出力の整形、およびUWI回答・ユーザーコメントの
書き込み拒否が同じ判定を使うため、`_atk`直下へ置く。
長時間の処理の実行中表示も、人が端末から直接使う呼び出しかを同じ判定から導く。
"""

import os
import sys
from collections.abc import Mapping
from typing import TextIO

AGENT_ENVIRONMENT_VARIABLES = ("AI_AGENT", "CODEX_CI", "CLAUDECODE", "CURSOR_AGENT")
"""コーディングエージェントの実行環境を示す環境変数。

いずれか1つでも設定されていればエージェント環境とみなす。
"""


def is_agent_environment(environment: Mapping[str, str] | None = None) -> bool:
    """コーディングエージェントの実行環境から起動されたかを返す。"""
    values = os.environ if environment is None else environment
    return any(name in values for name in AGENT_ENVIRONMENT_VARIABLES)


def is_human_terminal(stream: TextIO | None = None) -> bool:
    """人が端末から直接使う呼び出しか（エージェント環境でなく、標準エラーが端末）を返す。

    処理中・待機中の表示はこの判定が真の場合だけ標準エラーへ書く。
    パイプ、ファイル、テストの捕捉などへ出力する呼び出しでは、結果の行だけを保つ。
    """
    if is_agent_environment():
        return False
    target = sys.stderr if stream is None else stream
    try:
        return target.isatty()
    except AttributeError, ValueError:
        return False

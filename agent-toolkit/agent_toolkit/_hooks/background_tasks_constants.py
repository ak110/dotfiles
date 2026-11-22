"""非同期起動と完了通知の識別子定義。"""

import re

# 非同期待機系ツール名。これらのtool_useで直前アシスタントターンが終了している場合は
# セッション継続中と判断する。
# Bashはrun_in_backgroundフラグで別途判定するため、ここには含めない。
_ASYNC_WAIT_TOOLS: frozenset[str] = frozenset({"Agent", "ScheduleWakeup", "CronCreate", "Monitor"})


_TASK_NOTIFICATION_RE = re.compile(r"<task-notification>.*?</task-notification>", re.DOTALL)


_MCP_BACKGROUND_NOTICE_RE = re.compile(
    r'MCP tool "[^"\n]+" is still running after [\d.]+s\. It was moved to the background as task\s+(\S+)'
)


"""MCP呼び出しが実行時間の上限で背景へ移ったときにホストが返す移行通知の形。

自身の呼び出しが返した識別子であり、`atk-mode`の`references/operations.md`「プロセス終了の安全規定」が
停止を許容する所有の根拠に当たる。

ホストの通知はテキストの先頭から始まる単独の本文として届く（Claude Codeのtranscriptで観測した形）。
ツールの出力本文（前景Bashの`stdout`、MCPの検索結果、委譲先の返却など）は同じ文言を任意の位置に含み得るため、
先頭から一致する場合だけを通知とし、本文の途中の一致は識別子として扱わない。
Bashの背景移行（実行上限、手動、`run_in_background`）は構造化した`backgroundTaskId`から得るため、文言では判定しない。
"""


_TRAILING_PUNCTUATION = ".,;:!?)]}\"'"


_TOOL_USE_ID_RE = re.compile(r"<tool-use-id>(toolu_[\w]+)</tool-use-id>")


_TASK_ID_RE = re.compile(r"<task-id>([^<]+)</task-id>")


# SendMessage背景再開時のtool_result text先頭に現れる固有マーカー（旧形式）。
# `Agent ... resumed from transcript in the background with your message.`形式で
# 出力されるため、`resumed from transcript in the background`を一致条件とする。
# 同期SendMessage応答は本文言を含まないため、背景再開ケースのみ加算される。
# 現行形式は本文言を持たず`toolUseResult.resumedAgentId`を持つため、本マーカーとの一致判定は
# `resumedAgentId`を欠く結果に対する後方互換のフォールバックとしてのみ用いる。
_SENDMESSAGE_BG_RESUME_MARKER = "resumed from transcript in the background"


_AGENT_ASYNC_LAUNCH_STATUS = "async_launched"


_AGENT_SYNC_COMPLETION_STATUSES: frozenset[str] = frozenset({"completed", "teammate_spawned"})


_AGENT_ASYNC_LAUNCH_MARKER = "Async agent launched successfully"


_TASK_STOP_SUCCESS_PREFIX = "Successfully stopped task"


# `AGENT_TOOLKIT_STOP_GATE_DEBUG`環境変数の真値集合。小文字一致で判定する。
_DEBUG_TRUTHY_VALUES: frozenset[str] = frozenset({"1", "true", "yes", "on"})


_PENDING_ASYNC_WORK_CACHE: dict[tuple[object, ...], str] = {}

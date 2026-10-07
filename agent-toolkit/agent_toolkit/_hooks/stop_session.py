"""Stop系のhookが共有する、Stop入力の解析と判定根拠の常時ログ。

常時ログ（`append_stop_log`）はINFO相当（呼び出し側が渡す最終判定ラベルと主要フラグ）を
`{tempdir}/claude-agent-toolkit-stop-{session_id}.log`へ1行ずつ追記し、1MB超過時に`.log.1`へ1世代ローリングする。
判定の詳細を調べるDEBUG相当の出力は`background_tasks`が環境変数`AGENT_TOOLKIT_STOP_GATE_DEBUG`の指定時だけ標準エラーへ書く。
"""

import collections.abc
import json
import pathlib
import tempfile
import time

from agent_toolkit._common.file_lock import locked_rotate_and_append as _locked_rotate_and_append


def _stop_log_path(session_id: str) -> pathlib.Path:
    """常時ログの出力先パスを返す。

    `{tempdir}/claude-agent-toolkit-stop-{session_id}.log`形式とする。
    セッション状態ファイル（`agent_toolkit/_common/session_state.py`）と同じtempdir配下に置き、
    hostごとに衝突しないようsession_idで分離する。
    """
    return pathlib.Path(tempfile.gettempdir()) / f"claude-agent-toolkit-stop-{session_id}.log"


def append_stop_log(session_id: str, decision: str, context: dict, *, max_bytes: int = 1_000_000) -> None:
    """Stop hookの最終判定根拠を常時ログへ1行追記する。

    `decision`は呼び出し側が渡す最終判定ラベル（`approve_no_env`・
    `approve_pending_async`・`approve_exit_invoked`・`approve_block_limit_reached`・
    `block_autonomous_exit`など）。`context`は任意のkey-valueの辞書で、
    `last_tool`・`launched`・`pending`・`pending_ids`等を呼び出し側が任意で埋める。

    出力形式: `{ISO8601時刻} decision={...} k1=v1 k2=v2 ...`（1行）。
    `session_id`が空の場合はログ書き込みをスキップする。
    書き込み失敗（権限不足等）はStop hook本体の動作へ影響させないため無視する。
    `max_bytes`はローテーション閾値の注入点で、テストから小さい値を渡してローテーション動作を検証できる。
    """
    if not session_id:
        return
    timestamp = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())
    fields = " ".join(f"{key}={value}" for key, value in context.items())
    line = f"{timestamp} decision={decision}" + (f" {fields}" if fields else "") + "\n"
    _locked_rotate_and_append(_stop_log_path(session_id), line, max_bytes)


def parse_stop_session(raw_stdin: str, approve: collections.abc.Callable[[], None]) -> tuple[str, dict] | None:
    """Stop系hook共通の前段処理。ペイロード解析とsession_id検証を行う。

    JSON解析失敗またはsession_id欠落時は`approve`を呼び出したうえで`None`を返す。
    正常時は`(session_id, payload)`を返す。`stop_hook_active`判定・環境変数判定等の
    後続分岐は呼び出し側ごとに判定順序（`autonomous_exit.py`は環境変数判定を
    `stop_hook_active`より先に行う等）が異なるため、本関数には含めず呼び出し側へ委ねる。
    """
    try:
        payload = json.loads(raw_stdin)
    except (json.JSONDecodeError, ValueError):
        approve()
        return None

    session_id = payload.get("session_id", "")
    if not isinstance(session_id, str) or not session_id:
        approve()
        return None

    return session_id, payload

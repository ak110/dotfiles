"""ホストの上限を超えて退避したシェル出力の抜粋を、未読と次の操作を示す本文へ置き換える。

Claude CodeのBashとPowerShellは、出力が上限を超えると全量を保存先へ書き、モデルへ渡すツール結果を
`<persisted-output>`と出力の先頭約2KBの抜粋へ置き換える。抜粋は取得結果の全体と同じ位置と形で届くため、
抜粋だけを受け取った実行主体は、保存先を読まずに抜粋を全体として扱う誤りを本文から避けられない。
PostToolUseの`updatedToolOutput`で`stdout`を通知の本文へ置き換えると、ホストはその本文を`Preview`として示し、
受け取る本文から元の出力の先頭が消える。保存先のファイルは元の全量を保持する。

退避の有無はホストが`tool_response`へ付ける`persistedOutputPath`で判定し、上限値をhook側で再実装しない。
CodexのPostToolUseは`persistedOutputPath`を持たない（出力は切り詰めて返る）ため発動しない。
"""

import pathlib

lazy from agent_toolkit._common import next_action as _next_action
lazy from agent_toolkit._common.session_state import read_state, update_state
lazy from agent_toolkit._hooks import message_format as _message_format
lazy from agent_toolkit._hooks.agent_id import resolve_hook_agent_id
lazy from agent_toolkit._hooks.full_reads import full_reads

_SHELL_TOOLS = frozenset({"Bash", "PowerShell"})
_HOOK_ID = "posttooluse"
_PENDING = "persisted_output_unread"
_EVIDENCE_LABELS = ("根拠:", "完了:", "検証結果:", "証拠:")


def _notice(path: str, size: object) -> str:
    measured = f"（{size}バイト）" if isinstance(size, int) and not isinstance(size, bool) else ""
    body = f"出力{measured}はホスト上限を超えて退避された。この本文には出力を含めていない。"
    fix = (
        f"後続の判断に使う場合は`{path}`を全量取得せよ。部分取得かエラーなら残りを取得してから進め。"
        "全体を使わない場合は、件数や対象を限定したコマンドで取得し直せ。"
    )
    return _message_format.llm_notice(_next_action.with_next_action(body, fix), _HOOK_ID, tag="notice")


def replacement(payload: dict) -> dict | None:
    """退避したBash・PowerShellの結果なら、`stdout`だけを通知の本文へ置き換えた`tool_response`の写しを返す。

    他の項目（`stderr`、`persistedOutputPath`、`persistedOutputSize`など）は保つ。退避していない結果、
    他のツールおよびPostToolUse以外のイベントでは`None`を返す。
    """
    if payload.get("hook_event_name", "PostToolUse") != "PostToolUse" or payload.get("tool_name") not in _SHELL_TOOLS:
        return None
    response = payload.get("tool_response")
    if not isinstance(response, dict):
        return None
    path = response.get("persistedOutputPath")
    if not isinstance(path, str) or not path.strip():
        return None
    return {**response, "stdout": _notice(path, response.get("persistedOutputSize"))}


def observe(payload: dict, session_id: str, tool_name: str, tool_input: dict, cwd: str) -> None:
    actor = resolve_hook_agent_id(payload)
    saved = read_state(session_id).get(_PENDING)
    existing = saved.get(actor, []) if isinstance(saved, dict) else []
    response = payload.get("tool_response")
    path = response.get("persistedOutputPath") if isinstance(response, dict) and tool_name in _SHELL_TOOLS else None
    added = {path} if isinstance(path, str) and pathlib.Path(path).is_absolute() else set()
    reads = full_reads(tool_name, tool_input, response, cwd, lambda item: str(item) in existing) if existing else []
    read = {str(item) for item in reads}
    if not added and not read:
        return

    def remember(state: dict) -> dict:
        actors = state.get(_PENDING)
        actors = dict(actors) if isinstance(actors, dict) else {}
        paths = set(actors.get(actor, []))
        actors[actor] = sorted((paths | added) - read)
        return {**state, _PENDING: actors}

    update_state(session_id, remember)


def unread_evidence(payload: dict, session_id: str, message: object) -> str | None:
    if not isinstance(message, str) or not session_id:
        return None
    saved = read_state(session_id).get(_PENDING)
    paths = saved.get(resolve_hook_agent_id(payload), []) if isinstance(saved, dict) else []
    for line in message.splitlines():
        if line.lstrip(" -*").startswith(_EVIDENCE_LABELS) and not any(word in line for word in ("未確認", "未取得", "保留")):
            for path in paths:
                if path in line:
                    return path
    return None

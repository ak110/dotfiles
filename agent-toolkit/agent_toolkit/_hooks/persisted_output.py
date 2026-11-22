"""ホストの上限を超えて退避したシェル出力の抜粋を、未読と次の操作を示す本文へ置き換える。

Claude CodeのBashとPowerShellは、出力が上限を超えると全量を保存先へ書き、モデルへ渡すツール結果を
`<persisted-output>`と出力の先頭約2KBの抜粋へ置き換える。抜粋は取得結果の全体と同じ位置と形で届くため、
抜粋だけを受け取った実行主体は、保存先を読まずに抜粋を全体として扱う誤りを本文から避けられない。
PostToolUseの`updatedToolOutput`で`stdout`を通知の本文へ置き換えると、ホストはその本文を`Preview`として示し、
受け取る本文から元の出力の先頭が消える。保存先のファイルは元の全量を保持する。

退避の有無はホストが`tool_response`へ付ける`persistedOutputPath`で判定し、上限値をhook側で再実装しない。
CodexのPostToolUseは`persistedOutputPath`を持たない（出力は切り詰めて返る）ため発動しない。
"""

lazy from agent_toolkit._common import next_action as _next_action
lazy from agent_toolkit._hooks import message_format as _message_format

_SHELL_TOOLS = frozenset({"Bash", "PowerShell"})
_HOOK_ID = "posttooluse"


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

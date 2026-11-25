import json

from agent_toolkit._common.send_to_user import from_repl
lazy from agent_toolkit._atk import managed_temp


def resend_guidance(
    session_id: str | None,
    message: object,
    *,
    tool_name: str,
    tool_use_id: object,
    kind: object,
    edit_body: bool = True,
) -> str:
    direct = "指摘を反映した本文をsend_to_userで送り直す" if edit_body else "原因を解消した後、同じ本文をsend_to_userで送り直す"
    if not session_id or not isinstance(message, str):
        return f"本文を書き出せなかった。{direct}。"
    try:
        area = managed_temp.create_session_temp("rejected-send-to-user", managed_temp.resolve_session_temp(session_id))
        path = area / "message.md"
        path.write_text(message, encoding="utf-8", newline="")
    except OSError, UnicodeError, managed_temp.ManagedTempError:
        return f"本文を書き出せなかった。{direct}。"
    if not from_repl(tool_use_id):
        return f"本文の保存先: `{path}`。{direct}。"
    text = f"file({json.dumps(path.as_posix(), ensure_ascii=False)}).text()"
    args = f"message: await {text}"
    if isinstance(kind, str):
        args += f", kind: {json.dumps(kind, ensure_ascii=False)}"
    command = f"await tools[{json.dumps(tool_name)}]({{{args}}})"
    action = (
        "保存した本文をREPLの `Read` ツールで読み（読んだ本文は出力せず）、`edit()` で指摘箇所だけ直し"
        if edit_body
        else "原因を解消した後、保存した本文は編集せず"
    )
    return f"本文の保存先: `{path}`。{action}、`{command}` で送り直す。"

import json

from agent_toolkit._common import send_misdetection
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
    check: str | None = None,
) -> str:
    direct = "指摘を反映した本文をsend_to_userで送り直す" if edit_body else "原因を解消した後、同じ本文をsend_to_userで送り直す"
    if not session_id or not isinstance(message, str):
        return f"本文を書き出せなかった。{direct}。"
    try:
        area = managed_temp.create_session_temp("rejected-send-to-user", managed_temp.resolve_session_temp(session_id))
        path = area / "message.md"
        path.write_text(message, encoding="utf-8", newline="")
    except OSError, UnicodeError, managed_temp.ManagedTempError:
        path = None
    location = (
        str(path) if path is not None else f"tool_use_id:{tool_use_id}" if isinstance(tool_use_id, str) and tool_use_id else ""
    )
    claim = (
        json.dumps([{"check": check, "reason": "誤検出と判断した理由"}], ensure_ascii=False)
        if check and location and send_misdetection.remember_rejection(session_id, check, message, location)
        else ""
    )
    if path is None:
        failure = f"本文を書き出せなかった。正しい検出なら{direct}。"
        if claim:
            return (
                f"{failure}誤検出なら元の呼び出し（`{location}`）の本文を変更せず、"
                f"同じ本文と既存の引数に `misdetection={claim}` を加えて再送する。"
            )
        return f"{failure}申告付き再送に必要な記録は作れなかった。" if check else f"本文を書き出せなかった。{direct}。"
    saved = f"本文の保存先: `{path}`。"
    if check and not claim:
        saved += "申告付き再送に必要な記録は作れなかった。"
    if not from_repl(tool_use_id):
        if claim:
            return (
                f"{saved}正しい検出なら{direct}。誤検出なら本文を変えず、同じ呼出しに `misdetection={claim}` を加えて再送する。"
            )
        return f"{saved}{direct}。"
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
    if claim:
        retry = f"await tools[{json.dumps(tool_name)}]({{{args}, misdetection: {claim}}})"
        return f"{saved}正しい検出なら{action}、`{command}` で再送する。誤検出なら本文を編集せず、`{retry}` で再送する。"
    return f"{saved}{action}、`{command}` で送り直す。"

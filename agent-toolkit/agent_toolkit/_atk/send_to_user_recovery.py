import json
import os
import re
import stat

from agent_toolkit._common import send_misdetection
from agent_toolkit._common.send_to_user import from_repl
lazy from agent_toolkit._atk import managed_temp

_BODY_REFERENCE = re.compile(r"rejected-send-to-user-[0-9a-z_-]{8}", re.ASCII)


def load_body_reference(session_id: str, reference: object) -> str:
    if not session_id or not isinstance(reference, str) or not _BODY_REFERENCE.fullmatch(reference):
        raise ValueError("body_refにはこの会話に保存された本文の参照を指定する")
    try:
        root = managed_temp.find_session_temp(session_id)
        if root is None:
            raise ValueError("この会話の保存領域を確認できない")
        path = managed_temp.contained_target(root / reference / "message.md")
        expected = path.lstat()
        if (
            not stat.S_ISREG(expected.st_mode)
            or expected.st_nlink != 1
            or (os.name == "posix" and expected.st_uid != os.geteuid())
        ):
            raise ValueError("本文の所有者か通常ファイルであることを確認できない")
        with path.open("rb") as stream:
            opened = os.fstat(stream.fileno())
            if (opened.st_dev, opened.st_ino) != (expected.st_dev, expected.st_ino):
                raise ValueError("保存本文が解決中に変わった")
            return stream.read().decode("utf-8")
    except (OSError, UnicodeError, managed_temp.ManagedTempError) as error:
        raise ValueError("この会話に属する保存本文を読めない") from error


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
    args = f"body_ref: {json.dumps(path.parent.name)}"
    if isinstance(kind, str):
        args += f", kind: {json.dumps(kind, ensure_ascii=False)}"

    def call(arguments: str) -> str:
        return (
            f"await tools[{json.dumps(tool_name)}]({{{arguments}}})" if from_repl(tool_use_id) else f"send_to_user({arguments})"
        )

    command = call(args)
    action = "必要なら保存した本文を読み、指摘箇所だけ直し" if edit_body else "原因を解消した後、保存した本文は編集せず"
    if claim:
        retry = call(f"{args}, misdetection: {claim}")
        return f"{saved}正しい検出なら{action}、`{command}` で再送する。誤検出なら本文を編集せず、`{retry}` で再送する。"
    return f"{saved}{action}、`{command}` で送り直す。"

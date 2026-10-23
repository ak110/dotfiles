"""人間の発話の後に可視の応答本文が無いStopへ一度通知する。

判定情報はtranscriptの人間入力の印と、textブロックと`send_to_user`の`message`の有無から確定する。
拡張思考の表示をエージェントから観測できないため、応答はユーザーへ届く本文の有無で判定する。
ツール呼び出しより前の地の文が要約へ置き換わる事象をこの判定で検出することはしない。発話したつもりで届いていない事象は
発話を契機とする判定では捉えられず、原文を`send_to_user`で運ぶ規範とツールで扱う（2026年10月6日、ユーザーの判断）。
回答の意味は判定せず、拡張思考とツール呼び出しは本文へ数えない。
例外は人間の入力が単純な了承（ユーザーが原文で示した「単純に了承したパターン」）だけの場合で、了承の後にツールを呼ばず、
`…`だけの本文で終えるStopは遮断しない。規範（`rules-main.md`「ユーザー向け発話ルール」と
`rules-main.claude-code.md`「ターンの終え方」）は伝える内容が無い場合に`…`だけで終えることを許し、了承には伝える回答が無いためである。
了承の判定は名指しした語の一覧への一致で閉じず、入力の全体をNFKC正規化と`casefold`の後に、了承を表す語、丁寧語・終助詞、
末尾の記号・絵文字、読点による了承語どうしの連結だけから成るかを構成規則で確かめる。`?`は受理する記号に含めないため、問いは了承に当たらない。
了承の後にツールを呼んだ場合は、観測した結果を伝える内容として持ち得て、フックはその有無を区別できないため、本文を求める。
同じターンで可視本文を出力すれば解除できる。通知は伝える予定だった内容の出力を案内する。
同じ人間入力の不足は共通noticeで反復を抑止し、新しい入力と解消後の再発を区別する。

通知を置く根拠: 発話本文を可視の本文へ置く規範を加えた後も同じ欠落が再発した。
本文の有無はtranscriptから機械的に確定でき、本文の出力で回復できるため非遮断の支援とする。基準は
`agent-toolkit:writing-standards`の`references/claude-hooks-block-warn.md`「遮断と警告の選択」にある。

委譲先での実行可否: ユーザーへの応答責務を持つメインだけを対象とし、
agent_idと委譲先の環境印でサブエージェントと委譲先を除く。
"""

import json
import re
import unicodedata

from agent_toolkit._hooks import agent_id, notice
from agent_toolkit._hooks import stop_session as _stop_session
from agent_toolkit._hooks.notice import _WARN_TAG

# 関数の実行時にだけ使う依存は、使用時まで読み込まない。
lazy from agent_toolkit._common import message_format as _message_format
lazy from agent_toolkit._common import transcript
lazy from agent_toolkit._hooks.host import is_codex_payload

_notice = notice.formatter("user_response_advisor", default_tag=_WARN_TAG)

# 了承の構成規則。NFKC正規化と`casefold`の後の入力へ適用する。
# 了承を表す語に丁寧語・終助詞が続く形を1単位とし、読点で単位を連結した入力の全体だけを了承とする。
# 語の集合は概念の代表であり、単純な了承に当たる言い回しを受理できない場合はここへ加える。
_ACKNOWLEDGEMENT_WORD = (
    r"(?:ok(?:ay|ey)?|oke|おk|おけ|おっけ|おーけ|オーケ|オッケ|オケ|りょ(?:う(?:かい)?)?|了解|了承|承知|把握|"
    r"わか(?:った|りました|り)|分か(?:った|りました|り)|はい|うん|ええ|いいよ|いいです|いいね|"
    r"yes|yep|yeah|sure|roger|ラジャ)"
)
_ACKNOWLEDGEMENT_SUFFIX = r"(?:いたしました|しました|でした|です|っす|す|だよ|だ|よ|ね|な|ー|~|〜)*"
_ACKNOWLEDGEMENT = re.compile(
    rf"{_ACKNOWLEDGEMENT_WORD}{_ACKNOWLEDGEMENT_SUFFIX}(?:[、,]\s*{_ACKNOWLEDGEMENT_WORD}{_ACKNOWLEDGEMENT_SUFFIX})*"
)
# 了承の末尾に付く記号。問いを表す`?`は含めない。
_TRAILING_MARKS = frozenset("!。.・…~〜ー♪")
_TRAILING_CATEGORIES = frozenset({"So", "Sk", "Mn", "Me", "Cf"})


def _is_acknowledgement(prompt: str) -> bool:
    """入力の全体が単純な了承だけを伝えるかを構成規則で判定する。"""
    text = unicodedata.normalize("NFKC", prompt).strip().casefold()
    end = len(text)
    while end and (
        text[end - 1] in _TRAILING_MARKS
        or text[end - 1].isspace()
        or unicodedata.category(text[end - 1]) in _TRAILING_CATEGORIES
    ):
        end -= 1
    return _ACKNOWLEDGEMENT.fullmatch(text[:end]) is not None


def _calls_tool(message: object) -> bool:
    """assistantのメッセージがツール呼び出しを含むかを返す。"""
    content = message.get("content") if isinstance(message, dict) else None
    return isinstance(content, list) and any(isinstance(block, dict) and block.get("type") == "tool_use" for block in content)


def evaluate(payload_text: str) -> tuple[str, str]:
    """最新の人間の発話に本文が無い場合だけ、本文の出力を求める。"""
    resolved = _stop_session.parse_stop_session(payload_text, lambda: None)
    if resolved is None:
        return "approve", ""
    session_id, payload = resolved
    notice.set_warning_session_id(session_id)
    if not agent_id.is_main_agent_context(payload):
        return "approve", ""
    path = payload.get("transcript_path")
    if not isinstance(path, str) or not path:
        return "approve", ""
    lines = transcript.read_transcript_lines(path)
    if lines is None:
        return "approve", ""

    needs_response = False
    input_subject = ""
    # 最後の可視本文より後の人間の入力が全て了承か、その後にツールを呼んだか、`…`だけの本文を出力したかを記録する。
    acknowledged_only = False
    called_tool = False
    ellipsis_only = False
    for index, line in enumerate(lines):
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(entry, dict) or entry.get("isSidechain"):
            continue
        prompt = _input_text(entry)
        if prompt is not None:
            if _is_human_input(entry) and not entry.get("isMeta") and not _is_hook_input(prompt):
                input_subject = f"{index}|{line}"
                if not needs_response:
                    acknowledged_only, called_tool, ellipsis_only = True, False, False
                acknowledged_only = acknowledged_only and _is_acknowledgement(prompt)
                needs_response = True
            elif not _is_human_input(entry) and prompt.lstrip().startswith("<task-notification>"):
                needs_response = False
        elif entry.get("type") == "assistant":
            message = entry.get("message")
            if entry.get("isApiErrorMessage") or isinstance(message, dict) and message.get("model") == "<synthetic>":
                continue
            text = transcript.visible_assistant_text(message)
            if any(not char.isspace() and char != "…" for char in text):
                needs_response = False
            elif needs_response:
                called_tool = called_tool or _calls_tool(message)
                ellipsis_only = ellipsis_only or "…" in text

    if not needs_response or (acknowledged_only and not called_tool and ellipsis_only):
        return "approve", ""
    _stop_session.append_stop_log(session_id, "notify_missing_user_response", {})
    delivery = "応答本文" if is_codex_payload(payload) else "mcp__agent-toolkit__send_to_user（無い環境では応答本文）"
    body = _notice(
        "人間の発話の後に、ユーザーへ届いた応答本文が無い。",
        fix=f"伝える予定だった回答・判断・確認結果を{delivery}で届ける。",
        removable_cause=True,
        subject=input_subject,
    )
    return ("notify", body) if body else ("approve", "")


def _input_text(entry: dict) -> str | None:
    """人間の発話とtask-notificationを識別する文字列入力を返す。"""
    if entry.get("type") == "user":
        message = entry.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        return content if isinstance(content, str) else None
    if entry.get("type") == "attachment":
        attachment = entry.get("attachment")
        if isinstance(attachment, dict) and attachment.get("type") == "queued_command":
            prompt = attachment.get("prompt")
            return prompt if isinstance(prompt, str) else None
    return None


def _is_hook_input(prompt: str) -> bool:
    """機械生成の境界を持つ入力を人間の発話から除く。"""
    return _message_format.starts_with_auto_element(prompt)


def _is_human_input(entry: dict) -> bool:
    """ホストが人間の入力として記録した要素だけを応答の契機にする。"""
    source = entry.get("attachment") if entry.get("type") == "attachment" else entry
    origin = source.get("origin") if isinstance(source, dict) else None
    return isinstance(origin, dict) and origin.get("kind") == "human"

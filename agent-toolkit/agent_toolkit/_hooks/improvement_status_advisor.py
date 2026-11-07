"""直接応答の`気付いた改善点:`行で、原因の未調査を理由に`AWI化ステータス: 不要`とした報告へ再確認を促す。

`share/rules-main.md`「作業中の改善点の報告」は、処置とAWI化を決める前に担当・処置を決める最小調査を求める。
原因を調べていないことを見送りの理由にした報告は、その調査を省いたまま処置不要と判断している。
判定は理由の冒頭が決まった語句と一致するかだけで行い、理由の意味は推定しない。誤検出があり得るため遮断せず通知する。
報告行の抽出は`runtime_inserted.reported_improvement_lines`を使い、コード例と引用を報告から除く。
同じ判定を`send_to_user`の送信前にも行う（`hooks/send_to_user.tsx`の`UNINVESTIGATED_REASON`）。判定語を変える場合は両方を合わせる。

委譲先での実行可否: 委譲先は改善点を委譲元へ返却し、委譲元が処置とAWI化を判断するため判定しない。
"""

import json
import re

from agent_toolkit._common import runtime_inserted
from agent_toolkit._hooks.agent_id import is_main_agent_context
from agent_toolkit._hooks.notice import _WARN_TAG, set_warning_session_id
from agent_toolkit._hooks.notice import formatter as _notice_formatter

_HOOK_ID = "improvement_status_advisor"
_notice = _notice_formatter(_HOOK_ID, default_tag=_WARN_TAG)

# `不要`の理由の先頭に置く区分名。
# 定義は`agent-basics:bugfix`の`references/root-cause-prevention.md`「再発防止策の必須性」にある。
_UNNEEDED_CATEGORIES = ("非欠陥", "処置済み", "被覆", "見送り")
_UNNEEDED_STATUS = re.compile(r"AWI化ステータス:\s*不要[ \t]*")
# 原因を調べていないことを見送りの理由にした冒頭。
# 処置・担当を定めた理由や「未調査を理由に見送らない」の説明は冒頭が一致しない。
# 「原因は未確定だが、既存AWIへ引き継ぎ済み」のように逆接が続く形は、
# 未確定を認めたうえで後段の処置・担当を理由にしているため除く。
UNINVESTIGATED_REASON = re.compile(
    r"^(?:原因(?:が|は)?(?:まだ)?(?:未調査|未確定|未特定|不明)|原因を(?:まだ)?(?:調べて|調査して|特定して)いない|未調査(?![をにと]))"
    r"(?!(?:だ|です)?(?:が|けれど|けど)|ながら|ものの|でも)"
)


def uninvestigated_unneeded_reasons(text: str) -> list[str]:
    """報告行のうち、`不要`の理由が原因の未調査を根拠にしている行を返す。"""
    found: list[str] = []
    for line in runtime_inserted.reported_improvement_lines(text):
        for matched in _UNNEEDED_STATUS.finditer(line):
            reason = line[matched.end() :]
            category = next((name for name in _UNNEEDED_CATEGORIES if reason.startswith(name)), "")
            reason = reason[len(category) :].lstrip(" \t、,")
            if UNINVESTIGATED_REASON.match(reason):
                found.append(line)
                break
    return found


def evaluate(payload_text: str) -> tuple[str, str]:
    """Claude Codeメインの直接応答に該当する報告行があれば、非遮断の通知を返す。"""
    try:
        payload = json.loads(payload_text)
    except ValueError, TypeError:
        return "", ""
    if not isinstance(payload, dict) or not is_main_agent_context(payload):
        return "", ""
    text = payload.get("last_assistant_message")
    if not isinstance(text, str):
        return "", ""
    lines = uninvestigated_unneeded_reasons(text)
    if not lines:
        return "", ""
    session_id = payload.get("session_id")
    set_warning_session_id(session_id if isinstance(session_id, str) else "")
    return "notify", _notice(
        "`AWI化ステータス: 不要`の理由が、原因を調べていないことを見送りの根拠にしている報告がある。"
        "未調査は処置不要の根拠にならない。該当行: " + " / ".join(lines),
        removable_cause=True,
        subject="\n".join(lines),
        fix=(
            "直接的原因と影響を確かめる最小調査をその場で行い、処置と担当を決めて報告を更新せよ。"
            "担当範囲外なら、観測と残る問いを`agents_server`の委譲先かAWIへ引き継げ。"
        ),
    )

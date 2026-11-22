"""報告の送達の前後に、改善点の報告の書式を案内し、未調査を理由にした`不要`へ再確認を促す。

改善点の報告（`atk-mode`の`playbooks/main-reporting.md`「作業中の改善点の報告」）は、処置とAWI化を決める前に担当・処置を決める最小調査を求める。
原因を調べていないことを見送りの理由にした報告は、その調査を省いたまま処置不要と判断している。
逸脱の報告は、今回の事象へ`agent-basics:bugfix`を適用し、同スキルの「記録」の行頭語（`処置:`と条件付きの`再発防止:`）で
今回の是正と再発防止の採否を示す。
判定は報告行の語句の一致だけで行い、意味は推定しない。誤検出があり得るため遮断せず通知する。
報告行の抽出は`runtime_inserted`（`report_lines`）を使い、コード例と引用を報告から除く。
未調査の理由の判定は`send_to_user`の送達後にも行う（`hooks/send_to_user.tsx`の`UNINVESTIGATED_REASON`）。
判定語を変える場合は両方を合わせる。
書式の案内は送達前（`main`が受ける`send_to_user`の送信前照会）と送達後のStopの両方で出す。
逸脱の報告に欠けた記録の行は、`send_to_user`の送達後のツール結果（`hooks/send_to_user.tsx`の`RECORD_LINE_CHECKS`）だけが案内する。
Stopの通知はターンの継続を強制し、記録の行は以後の報告でしか補えないため、Stopでは案内しない。
Stopの通知は、同じターンで一度継続した後（`stop_hook_active`が真）には繰り返さない。

委譲先での実行可否: 委譲先は改善点と逸脱を委譲元へ返却し、委譲元が処置とAWI化を判断するため判定しない。
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
# 書式と区分の定義元は`atk-mode`の`playbooks/main-reporting.md`「進捗報告」にある。通知は区分を本文に挙げる。
_UNNEEDED_CATEGORIES = ("非欠陥", "処置済み", "被覆", "所有範囲外", "見送り")
_UNNEEDED_STATUS = re.compile(r"AWI化ステータス:\s*不要[ \t]*")
_CATEGORY_STATUS = re.compile(r"AWI化ステータス:\s*(不要|予定)[ \t]*")
_DEFERRAL_FIELDS = ("作り込み側の候補", "見逃し側の候補", "比較結論")
_DEFERRAL_FIELD = re.compile(r"(作り込み側の候補|見逃し側の候補|比較結論)\s*[:：]\s*([^;；\n]*)")
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


def _format_notice(lines: list[str]) -> str:
    """改善点の報告行の区分名と見送りの比較項目の書式の誤りを、最初の1件だけ返す。"""
    categories = {"不要": _UNNEEDED_CATEGORIES, "予定": ("判断待ち", "認可待ち", "入力待ち", "書込競合")}
    for line in lines:
        for matched in _CATEGORY_STATUS.finditer(line):
            if not line[matched.end() :].startswith(categories[matched.group(1)]):
                return _notice(
                    "改善点報告の区分名が、報告行の書式と一致しない。",
                    removable_cause=True,
                    subject=line,
                    fix=f"報告行を`AWI化ステータス: {matched.group(1)} <区分> "
                    + ("<理由>" if matched.group(1) == "不要" else "<再開条件>")
                    + "`の形に直せ。区分は"
                    + "・".join(f"`{name}`" for name in categories[matched.group(1)])
                    + "から選べ。",
                )
            if line[matched.end() :].startswith("見送り") and matched.group(1) == "不要":
                report = line[matched.end() :].split("AWI化ステータス:", 1)[0]
                values = {name: value.strip(" \t。）」)") for name, value in _DEFERRAL_FIELD.findall(report)}
                missing_fields = [name for name in _DEFERRAL_FIELDS if not values.get(name)]
                if missing_fields:
                    return _notice(
                        "見送り報告の比較項目が空または欠落している: " + "、".join(missing_fields) + "。",
                        removable_cause=True,
                        subject=line,
                        fix="同じ報告行に`作り込み側の候補:`・`見逃し側の候補:`・`比較結論:`を、"
                        "セミコロンで区切って記録せよ。候補と結論の妥当性は担当が判断せよ。",
                    )
    return ""


def format_guidance_notice(payload: object) -> str:
    """最上位メインの報告へ、同じターンで直せる改善点の報告の書式の誤りを返す。"""
    if not isinstance(payload, dict) or not is_main_agent_context(payload):
        return ""
    text = payload.get("last_assistant_message")
    session_id = payload.get("session_id")
    if not isinstance(text, str) or not isinstance(session_id, str) or not session_id:
        return ""
    improvements = runtime_inserted.reported_improvement_lines(text)
    if not improvements:
        return ""
    set_warning_session_id(session_id)
    return _format_notice(improvements)


def evaluate(payload_text: str) -> tuple[str, str]:
    """Claude Codeメインの直接応答に該当する報告行があれば、非遮断の通知を返す。"""
    try:
        payload = json.loads(payload_text)
    except ValueError, TypeError:
        return "", ""
    if not isinstance(payload, dict) or payload.get("stop_hook_active") is True or not is_main_agent_context(payload):
        return "", ""
    text = payload.get("last_assistant_message")
    if not isinstance(text, str):
        return "", ""
    lines = uninvestigated_unneeded_reasons(text)
    guidance = format_guidance_notice(payload)
    if not lines:
        return ("notify", guidance) if guidance else ("", "")
    session_id = payload.get("session_id")
    set_warning_session_id(session_id if isinstance(session_id, str) else "")
    reason_notice = _notice(
        "`AWI化ステータス: 不要`の理由が、原因を調べていないことを見送りの根拠にしている報告がある。"
        "該当行: " + " / ".join(lines),
        removable_cause=True,
        subject="\n".join(lines),
        fix="担当と処置を決めるのに足りる最小調査をし、処置・担当を決めてから、その結果に基づいて報告を更新せよ。",
    )
    return "notify", "\n\n".join(filter(None, (guidance, reason_notice)))


def main(payload_text: str) -> int:
    """`send_to_user`の送達前照会へ、改善点の報告の書式の案内を返す。"""
    payload = json.loads(payload_text)
    notice = format_guidance_notice(payload)
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": notice}}, ensure_ascii=False))
    return 0

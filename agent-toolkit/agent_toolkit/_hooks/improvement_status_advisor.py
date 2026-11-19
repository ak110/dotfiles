"""報告の送達の前後に、判定資料の取得と改善点の報告の書式を案内し、未調査を理由にした`不要`へ再確認を促す。

`share/rules/rules-main.md`「作業中の改善点の報告」は、処置とAWI化を決める前に担当・処置を決める最小調査を求める。
原因を調べていないことを見送りの理由にした報告は、その調査を省いたまま処置不要と判断している。
逸脱の報告は、今回の事象へ`agent-basics:bugfix`を適用し、同スキルの「記録」の行頭語（`処置:`と条件付きの`再発防止:`）で
今回の是正と再発防止の採否を示す。
判定は報告行の語句の一致だけで行い、意味は推定しない。誤検出があり得るため遮断せず通知する。
報告行の抽出と判定語は`runtime_inserted`（`report_lines`・`REPORT_CUES`）を使い、コード例と引用を報告から除く。
未調査の理由の判定は`send_to_user`の送達後にも行う（`hooks/send_to_user.tsx`の`UNINVESTIGATED_REASON`）。
判定語を変える場合は両方を合わせる。
判定資料の取得案内は送達前（`main`が受ける`send_to_user`の送信前照会）だけで出す。送達後のStopで案内しても、
報告は既に届いていて見直しが反映されないため、Stopでは取得案内を出さず、改善点の報告行の書式・理由だけを通知する。
逸脱の報告に欠けた記録の行は、`send_to_user`の送達後のツール結果（`hooks/send_to_user.tsx`の`RECORD_LINE_CHECKS`）だけが案内する。
Stopの通知はターンの継続を強制し、記録の行は以後の報告でしか補えないため、Stopでは案内しない。
Stopの通知は、同じターンで一度継続した後（`stop_hook_active`が真）には繰り返さない。

委譲先での実行可否: 委譲先は改善点と逸脱を委譲元へ返却し、委譲元が処置とAWI化を判断するため判定しない。
"""

import json
import os
import pathlib
import re

from agent_toolkit._common import mcp_environment, read_triggers, runtime_inserted
from agent_toolkit._common.plugin_roots import resolve_plugin_root
from agent_toolkit._common.session_state import read_state
from agent_toolkit._hooks import document_reads, plugin_resources, rules_context
from agent_toolkit._hooks.agent_id import is_main_agent_context
from agent_toolkit._hooks.host import is_codex_payload
from agent_toolkit._hooks.notice import _WARN_TAG, set_warning_session_id
from agent_toolkit._hooks.notice import formatter as _notice_formatter

_HOOK_ID = "improvement_status_advisor"
_notice = _notice_formatter(_HOOK_ID, default_tag=_WARN_TAG)

# `不要`の理由の先頭に置く区分名。
# 書式と区分は`share/rules/rules-main.md`「進捗報告」のテンプレート直下にある。
_UNNEEDED_CATEGORIES = ("非欠陥", "処置済み", "被覆", "見送り")
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

# 報告の手掛かり（`runtime_inserted.REPORT_CUES`）ごとに、送達前に取得を確かめる`agent-basics`の資料と、取得後に行う確認。
_CUE_DOCUMENTS = (
    (
        "deviation",
        "skills/bugfix/SKILL.md",
        "逸脱を示す報告。今回の事象へ5つの問いを適用する。以前に読んだ本文の再利用は今回の適用に当たらない",
    ),
    (
        "absence",
        "skills/bugfix/references/spread.md",
        "仕組みや規範が無いことを述べる報告。「同じ害への既存の対策」に従い、既存の契約・規範・検査を害を表す語で検索して主張と比べる",
    ),
    (
        "no_change",
        "skills/bugfix/references/causes.md",
        "変えていないことを述べる報告。「意図と経緯の調べ方」に従い、現行の値に加えて変更履歴と要求の原文を確かめて回答と比べる",
    ),
)
_IMPROVEMENT_PURPOSE = "改善点の報告。処置とAWI化ステータスを見直す"


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
                    "改善点報告の区分名が、常時規範のテンプレートの書式と一致しない。",
                    removable_cause=True,
                    subject=line,
                    fix="`share/rules/rules-main.md`「進捗報告」のテンプレート直下の書式・区分で報告行を直せ。"
                    "書式の修正だけなら判定資料の追加取得は不要である。",
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


def _acquired(records: dict, path: pathlib.Path) -> bool:
    """現在の版の全文取得か`Skill`起動の記録があるかを返す。"""
    if document_reads.acquisition_state(records, read_triggers.ReadTarget(path), "main") == "current":
        return True
    owners = records.get(document_reads.skill_invocation_key(path, document_reads.content_fingerprint(path)), [])
    return isinstance(owners, list) and "main" in owners


def _read_notice(payload: dict, session_id: str, cues: frozenset[str], *, improvements: bool) -> str:
    """報告の手掛かりと改善点の報告に対応する資料のうち、取得記録が無いものを案内する。"""
    state = read_state(session_id)
    root = plugin_resources.plugin_root()
    cwd = payload.get("cwd")
    cwd = cwd if isinstance(cwd, str) else ""
    basics = resolve_plugin_root("agent-basics", root, "codex" if is_codex_payload(payload) else "claude", project=cwd or None)
    wanted = [(basics / relative, purpose) for cue, relative, purpose in _CUE_DOCUMENTS if cue in cues]
    if improvements:
        wanted += [
            (basics / "skills/bugfix/references/prevention.md", _IMPROVEMENT_PURPOSE),
            (root / "skills/delegation/references/waiting-and-monitoring.md", _IMPROVEMENT_PURPOSE),
        ]
        startup = os.environ.get(mcp_environment.SESSION_START_INPUT_ENV, "{}")
        try:
            source = json.loads(startup)
        except ValueError:
            source = {}
        process_wi = state.get("session_title_kind") in {"process-wi", "single-lane"} or (
            isinstance(source, dict)
            and (source.get("source") == "process-loop" or source.get("skill") == "agent-toolkit:process-wi")
        )
        if process_wi:
            wanted.append((root / "skills/process-wi/references/immediate-fix.md", _IMPROVEMENT_PURPOSE))
    records = state.get(rules_context.OPERATION_SKILL_READY_KEY, {})
    records = records if isinstance(records, dict) else {}
    # 導入済みの配布物に無い資料は案内しても取得できないため、実在する資料だけを照会する。
    missing = [(path, purpose) for path, purpose in wanted if path.is_file() and not _acquired(records, path)]
    if not missing:
        return ""
    lines = "\n".join(f"- {document_reads.reference_name(path, cwd=cwd)}: {purpose}。" for path, purpose in missing)
    return _notice(
        "報告の判定に使う資料の取得を確認できない。\n" + lines,
        removable_cause=True,
        subject="\n".join(str(path) for path, _ in missing),
        fix="各行の資料を取得し、その行の確認をしてから報告を見直せ。必要なら報告を訂正せよ。"
        "語の一致で選んだ案内のため、報告に当たらない行は対応不要である。",
    )


def read_guidance_notice(payload: object, *, delivered: bool = False) -> str:
    """最上位メインの報告へ、改善点の書式の誤りと、報告の形に応じた案内を返す。

    送達前（`delivered`が偽）は、未取得の判定資料を既存の取得記録から案内する。
    送達後のStop（`delivered`が真）は取得案内を返さず、同じターンで直せる改善点の書式の誤りだけを返す。
    """
    if not isinstance(payload, dict) or not is_main_agent_context(payload):
        return ""
    text = payload.get("last_assistant_message")
    session_id = payload.get("session_id")
    if not isinstance(text, str) or not isinstance(session_id, str) or not session_id:
        return ""
    improvements = runtime_inserted.reported_improvement_lines(text)
    cues = frozenset() if delivered else runtime_inserted.report_cues(text)
    if not improvements and not cues:
        return ""
    set_warning_session_id(session_id)
    format_notice = _format_notice(improvements)
    if delivered:
        return format_notice
    # 書式の修正だけで済む改善点の報告へは、判定資料の追加取得を案内しない。
    read_notice = _read_notice(payload, session_id, cues, improvements=bool(improvements) and not format_notice)
    return "\n\n".join(filter(None, (format_notice, read_notice)))


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
    guidance = read_guidance_notice(payload, delivered=True)
    if not lines:
        return ("notify", guidance) if guidance else ("", "")
    session_id = payload.get("session_id")
    set_warning_session_id(session_id if isinstance(session_id, str) else "")
    reason_notice = _notice(
        "`AWI化ステータス: 不要`の理由が、原因を調べていないことを見送りの根拠にしている報告がある。"
        "該当行: " + " / ".join(lines),
        removable_cause=True,
        subject="\n".join(lines),
        fix="`share/main-improvements.md`「作業中の改善点の報告」に従い、最小調査と処置・担当の決定をして報告を更新せよ。",
    )
    return "notify", "\n\n".join(filter(None, (guidance, reason_notice)))


def main(payload_text: str) -> int:
    """`send_to_user`の送達前照会へ、報告の形に応じた取得案内を返す。"""
    payload = json.loads(payload_text)
    notice = read_guidance_notice(payload)
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": notice}}, ensure_ascii=False))
    return 0

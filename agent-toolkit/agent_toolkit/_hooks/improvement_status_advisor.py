"""改善点の報告前に判定資料の取得を案内し、未調査を理由にした`不要`へ再確認を促す。

`share/rules/rules-main.md`「作業中の改善点の報告」は、処置とAWI化を決める前に担当・処置を決める最小調査を求める。
原因を調べていないことを見送りの理由にした報告は、その調査を省いたまま処置不要と判断している。
判定は理由の冒頭が決まった語句と一致するかだけで行い、理由の意味は推定しない。誤検出があり得るため遮断せず通知する。
報告行の抽出は`runtime_inserted.reported_improvement_lines`を使い、コード例と引用を報告から除く。
同じ判定を`send_to_user`の送信前にも行う（`hooks/send_to_user.tsx`の`UNINVESTIGATED_REASON`）。判定語を変える場合は両方を合わせる。
判定資料の取得案内は送達前（`main`が受ける`send_to_user`の送信前照会）だけで出す。送達後のStopで案内しても、
報告は既に届いていて見直しが反映されないため、Stopでは取得案内を出さず、報告行の書式と理由の判定だけを通知する。

委譲先での実行可否: 委譲先は改善点を委譲元へ返却し、委譲元が処置とAWI化を判断するため判定しない。
"""

import json
import os
import re
import shlex

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


def read_guidance_notice(payload: object, *, include_reads: bool = True) -> str:
    """最上位メインの改善点報告で、書式の誤りと、未取得の判定資料を既存の取得記録から案内する。

    `include_reads`が偽の場合は、判定資料の取得案内を返さず書式の誤りだけを返す（送達後のStopの利用）。
    """
    if not isinstance(payload, dict) or not is_main_agent_context(payload):
        return ""
    text = payload.get("last_assistant_message")
    session_id = payload.get("session_id")
    if not isinstance(text, str) or not runtime_inserted.reported_improvement_lines(text):
        return ""
    if not isinstance(session_id, str) or not session_id:
        return ""
    categories = {"不要": _UNNEEDED_CATEGORIES, "予定": ("判断待ち", "認可待ち", "入力待ち", "書込競合")}
    for line in runtime_inserted.reported_improvement_lines(text):
        for matched in _CATEGORY_STATUS.finditer(line):
            if not line[matched.end() :].startswith(categories[matched.group(1)]):
                set_warning_session_id(session_id)
                return _notice(
                    "改善点報告の区分名が、常時規範のテンプレートの書式と一致しない。",
                    removable_cause=True,
                    subject=line,
                    fix="`share/rules/rules-main.md`「進捗報告」のテンプレート直下の書式・区分で報告行を直せ。"
                    "書式の修正だけなら判定資料の追加取得は不要である。",
                )
    if not include_reads:
        return ""
    state = read_state(session_id)
    root = plugin_resources.plugin_root()
    cwd = payload.get("cwd")
    basics = resolve_plugin_root(
        "agent-basics", root, "codex" if is_codex_payload(payload) else "claude", project=cwd if isinstance(cwd, str) else None
    )
    paths = [
        basics / "skills/bugfix/references/root-cause-prevention.md",
        root / "skills/delegation/references/waiting-and-monitoring.md",
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
        paths.append(root / "skills/process-wi/references/immediate-fix.md")
    records = state.get(rules_context.OPERATION_SKILL_READY_KEY, {})
    records = records if isinstance(records, dict) else {}
    missing = [
        path for path in paths if document_reads.acquisition_state(records, read_triggers.ReadTarget(path), "main") != "current"
    ]
    if not missing:
        return ""
    set_warning_session_id(session_id)
    commands = "\n".join(
        "Get-Content -LiteralPath '" + str(path).replace("'", "''") + "' -Encoding utf8"
        if os.name == "nt"
        else f"cat -- {shlex.quote(str(path))}"
        for path in missing
    )
    return _notice(
        "改善点の判定資料について、この文脈で全文取得したことを確認できない。"
        "部分表示やパイプ・変数を使った表示は取得操作に数えない。",
        removable_cause=True,
        subject="\n".join(map(str, missing)),
        fix=f"次の各コマンドで資料を全文読め。\n{commands}\n読んだ基準で処置とAWI化ステータスを見直し、必要なら報告を訂正せよ。",
    )


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
    guidance = read_guidance_notice(payload, include_reads=False)
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
    """Function hooksからの送達前照会へ、既存hookと同じ取得案内を返す。"""
    payload = json.loads(payload_text)
    notice = read_guidance_notice(payload)
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": notice}}, ensure_ascii=False))
    return 0

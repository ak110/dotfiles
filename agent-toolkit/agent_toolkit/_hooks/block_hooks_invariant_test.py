"""遮断するhookの一覧を固定し、遮断の追加をレビューで確認できる変更にする。  pyfltr: allow-comment

遮断は`agent-basics:writing-standards/references/claude-hooks-block-warn.md`「遮断と警告の選択」の条件
（認可・破壊・データの整合性・公開契約・ユーザーへ届く本文の言語）と、特定の手段の可否に依存しない解消の操作を満たす時に限る。
資料の取得やスキルの起動だけで解消する遮断は、観測できないホストや起動できない状況で回復できなくなった（2026-11-24、Antigravity）。
各項目の値は、スキルや資料の可否に依存しない解消の操作を書く。
"""

import pathlib
import re

_ROOT = pathlib.Path(__file__).resolve().parents[1]

ALLOWED_BLOCK_HOOKS = {
    "bugfix_gate": "送達は同じ本文の再発行で通る（重なる品質遮断と記録を共有）。Stopはstop_hook_activeと連続遮断の上限で終わる",
    "permission_denial_notice": "拒否1件につき質問・報告・終了のいずれかを1回止め、既存の拒否確認手順を示す",
    "hook_notice_response_gate": "`hook通知への対応:`の行を1行書けば通る。同じ警告の組では1回だけ止め、再発行で通る",
    "delivered_repost_gate": "本文から再掲を除けば通る。Stopは連続遮断の上限で終わる",
    "norm_excuse_gate": "同じ本文の再発行で通る（重なる品質遮断と記録を共有）",
    "process_loop_exit_gate": "Stopだけで、stop_hook_activeと連続遮断の上限で終わる",
    "repository-protection": "認可の境界。登録の編集可否を変えるのはユーザーの判断",
    "response-language": "本文を日本語で書き直せば通る。誤検出は同じ本文の再出力で停止できる",
    "pretooluse": "統合フック。安全・認可・公開契約の判定だけを遮断し、資料の取得の観測に依存する判定は警告にする",
    "run_skill_report_gate": "Stopだけで、連続遮断の上限で終わり、起動元が未保存を報告する",
    "subagent-stop": "空の返却だけを止め、stop_hook_activeの再呼出しで終わる",
}


def test_block_hooks_are_listed() -> None:
    found: set[str] = set()
    for path in _ROOT.rglob("*.py"):
        if path.name.endswith("_test.py"):
            continue
        text = path.read_text(encoding="utf-8")
        for literal, name in re.findall(r"(?:block_formatter|_block_notice_formatter)\(\s*(?:\"([^\"]+)\"|(\w+))\s*\)", text):
            constant = re.search(rf"^{name}\s*=\s*\"([^\"]+)\"", text, re.MULTILINE) if name else None
            found.add(literal or (constant.group(1) if constant else name))
    assert found == set(ALLOWED_BLOCK_HOOKS), "遮断するhookを増減する時は条件を確かめて一覧を更新する"

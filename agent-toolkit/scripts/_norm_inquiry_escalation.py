"""Claude Code agent-toolkit: 規範照会・是正要求検出用の共有辞書とマッチャー。

`user_prompt_submit.py`が唯一の消費者であり、本モジュールはパターン集と純粋関数を集約する。
`_scope_escalation.py`（`pretooluse.py`・`stop_advisor.py`から共有される）と同型の
モジュール構成を踏襲する。

カテゴリ定義および代表フレーズの規範説明は
`agent-toolkit/skills/agent-standards/references/norm-inquiry-escalation-phrases.md`
の隔離リファレンスを参照する。本モジュールから当該Markdownを読み込む処理は設けない。

本モジュールは軽量な依存のみで動作するため、
PEP 723 script headerも重量級依存も持たない。
"""

from __future__ import annotations

import re

# 疑問符相当の文末表現。「か」「かな」「？」等の文末表現を指す
# （agent-toolkit/rules/02-collaboration.md「メタ視点」バレット項の対象文言例と対応する）。
# 単独の「か」は文末（改行・句読点・文字列終端）に接する場合のみ疑問符扱いとする。
_QUESTION_END = r"(?:[？?]|かな[？?]?|のか[？?]?|だろうか[？?]?|か(?=[。、\n\s]|\Z))"

# 規範照会・是正要求検出パターン。
# `agent-toolkit/rules/02-collaboration.md`「メタ視点」バレット項が対象とする、
# 既存規範の変更経緯・遵守状況をユーザーが疑問文で問う発話（`norm-inquiry`）、
# および既存規範・実装の誤りを明示的に是正要求する発話（`correction-request`）を検出する。
# 単純な質問・要望一般（規範・ルール・既存挙動への言及を伴わないもの）は対象外とする。
_NORM_INQUIRY_PHRASES: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "norm-inquiry",
        re.compile(r"勝手に[^。\n？?]{0,15}(変え|変わ|変更)[^。\n？?]{0,10}" + _QUESTION_END),
    ),
    (
        "norm-inquiry",
        re.compile(r"(規範|ルール|方針|ポリシー)[^。\n？?]{0,15}違反[^。\n？?]{0,10}" + _QUESTION_END),
    ),
    (
        "norm-inquiry",
        re.compile(
            r"(説明した|言った|伝えた|指摘した)はず[^。\n？?]{0,25}"
            r"(忘れ|形骸化|いつの間にか)?[^。\n？?]{0,15}" + _QUESTION_END
        ),
    ),
    (
        "norm-inquiry",
        re.compile(r"(のはず|はず)(なん)?(だ|です)?(けど|よね|ですよね|なんだけど)"),
    ),
    (
        "norm-inquiry",
        re.compile(r"いつの間にか[^。\n]{0,10}(形骸化|忘れ|消え|変わ|なくな)"),
    ),
    (
        "correction-request",
        re.compile(
            r"(規範|ルール|方針|ポリシー|既存|現行|挙動|コード|実装|コメント|記述|文言|"
            r"ドキュメント|判断|対応|パターン|定義|条文)"
            r"[^。\n]{0,30}(直して|修正して)(ください|くれ|欲しい|もらえ)?"
        ),
    ),
    (
        "correction-request",
        re.compile(r"(間違っ|誤っ)(ている|ています|ていた|てた|てる|てます|た)"),
    ),
    (
        "correction-request",
        re.compile(r"(それ|そこ|その(対応|判断|実装))は(違う|誤り|間違い)[^。\n]{0,10}(直し|修正)"),
    ),
    (
        "correction-request",
        re.compile(
            r"(今後|以降|これから|次から)[^。\n]{0,10}(方針|ルール|規範|規定)"
            r"[^。\n]{0,10}(にして|とする|に変えて|変更)"
        ),
    ),
)


def _match_norm_inquiry_escalation(text: str) -> tuple[str, str] | None:
    """テキストへ`_NORM_INQUIRY_PHRASES`を照合し、最初に一致した`(category, matched_phrase)`を返す。

    状態（セッション状態キーの読み取り・クールダウン判定）は一切持たない純粋関数。
    入力はプロンプト全文（先頭行に限定しない）1件のみ。
    matched_phraseはパターンのマッチテキストそのまま。
    未検出時・非文字列入力時はNoneを返す。
    """
    if not isinstance(text, str) or not text:
        return None
    for category, pattern in _NORM_INQUIRY_PHRASES:
        m = pattern.search(text)
        if m is not None:
            return (category, m.group(0))
    return None

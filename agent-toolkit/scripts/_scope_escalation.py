"""Claude Code agent-toolkit: scope-escalation検出用の共有辞書とマッチャー。

`pretooluse.py`と`stop_advisor.py`の双方から参照する共有モジュール。
エントリポイントスクリプト間で直接importする構造を避けるため、
`_SCOPE_ESCALATION_PHRASES`・`_match_scope_escalation`・`_SCOPE_ESCALATION_ALTERNATIVES`を
本モジュールへ集約する。

カテゴリ定義および代表フレーズの詳細は
`agent-toolkit/skills/agent-standards/references/scope-escalation-phrases.md`
の隔離リファレンスを参照する。

本モジュールは軽量な依存のみで動作するため、
PEP 723 script headerも重量級依存も持たない。
"""

from __future__ import annotations

import re
from collections.abc import Iterable

# Stop経路（`stop_advisor.py`）の基本照合カテゴリ集合。
# 自由文脈の誤検出リスクが低い宣言型のみに限定する。
# スキル実行中は`_STOP_FOCUS_CATEGORIES_EXTENDED`へ切り替えて照合対象を拡張する。
# `pretooluse.py`側のWrite/Edit対象文書検査は全カテゴリを対象とし、本フィルタは適用しない。
_STOP_FOCUS_CATEGORIES: frozenset[str] = frozenset({"process-omission"})

# スキル実行中（plan-mode・apply-feedback・process-feedbacks等の起動フラグ成立時）に用いる拡張照合カテゴリ集合。
# スキル実行文脈では縮退表明を含む可能性が高いため、Stop経路の照合対象を広げる。
# SubagentStop経路も本集合と同一のSSOTとして参照する。
_STOP_FOCUS_CATEGORIES_EXTENDED: frozenset[str] = frozenset(
    {
        "process-omission",
        "async-wait",
        "single-session",
        "quality-tradeoff",
        "next-cycle-defer",
        "approach-confirm",
    }
)

# scope-escalation縮退誘発フレーズ検出パターン。
# 01-agent.md「セッション分割・別計画化は禁止する」節および「縮退表明は発行しない」項目で禁止される、
# 作業量・残コンテキスト・所要時間・修正コスト等を根拠としたユーザーへの打診、
# および規範違反を明示認識せず工程を省略・割愛する宣言を機械検出する。
_SCOPE_ESCALATION_PHRASES: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "workload",
        re.compile(r"作業量(的|面)?(で|に|が)?(困難|厳しい|多い|膨大|大きい)|作業残量(を考慮|が多い)"),
    ),
    (
        "single-session",
        re.compile(
            r"(本|この|単一)セッション(の|で|内|下)?(リソース|残|容量)?では?(?:.{0,15})?(完遂|完了|完結|遂行)(?:.{0,10})?(困難|厳しい|現実的では?ない|不可能|できない)"
        ),
    ),
    (
        "single-session",
        re.compile(r"規模(的に|として)?(本|この|単一)?セッション(の|で|内|下)?(?:.{0,15})?(困難|厳しい|現実的では?ない)"),
    ),
    (
        "approach-confirm",
        re.compile(
            r"(進め方を(確認|相談|決め|聞|教え)|完遂か最小限か|完遂は時間がかかる|規範遵守で時間がかかる|どう進めるべきか指示"
            r"|完遂を試みる[^。\n]{0,30}(保存|次回|後で|継続|再開|先送り))"
        ),
    ),
    ("split-execution", re.compile(r"分割(して|で)(進|対応|実装|完了|処理)")),
    (
        "context-shortage",
        re.compile(
            r"(残(り)?コンテキスト|ターン数(が増|を踏まえ|の自己推定|の上限)|対話往復(が増|を踏まえ|の上限)|これ以上のターン"
            r"|コンテキスト容量[^、。\n]{0,10}(超え|上回|不足)|実装完遂リスク[^、。\n]{0,10}(上回|超え|見合わ)"
            r"|次回?サイクル[^、。\n]{0,5}(持ち越し|送り|回))"
        ),
    ),
    (
        "defer-onset",
        re.compile(r"((着手|対応|実装)(を)?(延期|後回し|別途|別計画)|別作業(と|扱い|化)|別(issue|チケット|PR)(と|扱い|化))"),
    ),
    ("priority-consult", re.compile(r"(優先順位|スコープ|範囲)[^、。\n]{0,8}(相談|確認|聞|委ね|任せ|決め)")),
    ("scope-volume", re.compile(r"(対象|作業)(件数|範囲)が(多|広|膨大)")),
    (
        "pattern-conformance",
        re.compile(
            r"(既存パターン踏襲|本計画外|本計画スコープ外|広範改修要|現状維持|本タスク範囲外|本タスクスコープ外|本作業(範囲|スコープ)外"
            r"|主要[^、。\n]{0,10}(に絞|のみ実施|のみ対応|限定)"
            r"|代表(箇所|例|事例)[^、。\n]{0,8}(のみ|に限|限定)"
            r"|本サイクル[^、。\n]{0,10}(のみ|限定|に限)"
            r"|(今後|次回)(の)?改訂[^、。\n]{0,10}(随時|順次)?[^、。\n]{0,5}(是正|対応|反映))"
        ),
    ),
    (
        "process-omission",
        re.compile(
            r"(規範違反(として|を)(扱う|認識)|規範違反と認識した上で|規範違反を承知|規範違反は次回"
            r"|規範チェック[^、。\n]{0,10}スキップ|工程省略|工程を省略|割愛(する|します)"
            r"|本計画[^、。\n]{0,20}省略(する|します)"
            r"|(session-review|振り返り|観測事象)[^、。\n]{0,15}記録(する|して|に留)"
            r"|観測事象として[^、。\n]{0,10}(記録|残))"
        ),
    ),
    (
        "process-scale",
        re.compile(
            r"工程(?:規模|数|ceremony|セレモニー)?[^、。\n]{0,10}(多すぎ|大きすぎ|膨大|重すぎ|簡略化|圧縮する|省略する|バイパスする|スキップする)"
            r"|セレモニー[^、。\n]{0,10}(重すぎ|省略する|スキップする)"
            r"|(plan-mode|規範)[^、。\n]{0,10}ceremony[^、。\n]{0,10}(省略する|スキップする|バイパスする)"
        ),
    ),
    (
        "mitigation-in-adoption",
        re.compile(
            r"(機械化[^、。\n]{0,5}除外|Write検査[^、。\n]{0,5}(過剰|除外)|軽減版[^、。\n]{0,5}(採用|扱い)"
            r"|過剰部分[^、。\n]{0,5}除外|縮退版[^、。\n]{0,5}(採用|扱い)"
            r"|(実効性|実装コスト)[^、。\n]{0,15}(コスト高|見合わ))"
        ),
    ),
    (
        "async-wait",
        re.compile(
            r"((完了通知|完了報告)[^、。\n]{0,10}(待つ|待機|待って)"
            r"|サブエージェント[^、。\n]{0,10}(終了|完了|応答|通知)[^、。\n]{0,10}(待つ|待機|待って))"
        ),
    ),
    (
        "quality-gate-count",
        re.compile(
            r"hookブロック[^、。\n]*繰り返|lint違反[^、。\n]*膨大|違反件数[^、。\n]*進行困難|ブロック回数を踏まえ|修正量が多い"
        ),
    ),
    (
        "quality-tradeoff",
        re.compile(
            r"(規模が大きすぎ|品質(が|を)?維持(が|を)?(できない|困難)|工数(対効果|見合い|見合わ)"
            r"|時間的制約|時間コストを考慮|効率優先"
            r"|(極めて|非常に)?大規模になる"
            r"|規模[^、。\n]{0,20}(大規模|過大))"
        ),
    ),
    (
        "next-cycle-defer",
        re.compile(
            r"((次(サイクル|セッション|計画)|別セッション|独立(の)?セッション)(で|に)?(扱う|再評価|再検討|対応|送り)"
            r"|(スコープ|テーマ|計画)を超える"
            r"|今回(の)?(スコープ|対応|対象)(外|から外)"
            r"|(影響(が)?(大き|大)い|影響大)(ため|のため|により)"
            r"|現行アーキテクチャ(の)?(大幅|根本)(な?)(見直し|改修)"
            r"|後続(作業|対応|PR|チケット|issue)(で|に|へ)?(委ね|扱う|対応|送)"
            r"|次回対応(と|扱い|とする|に回す)"
            r"|次回起動(時)?[^、。\n]{0,15}(継続|実装|再開|対応|進行)"
            r"|外側(スキル)?[^、。\n]{0,10}(復帰|戻)"
            r"|保存して[^、。\n]{0,15}(次回|後で)[^、。\n]{0,10}(継続|再開))"
        ),
    ),
    (
        "plan-deferral-onset",
        # 計画ファイル本文の`## 変更内容`・`### エージェント判断`配下で、
        # 次の2条件をANDで満たす先送り含意パターンを検出する。
        # 条件(a): 「実装時」または「実装段階」の直後に
        #   「精査／選定／確定／評価／検討」等の未確定動詞が続く
        # 条件(b): 文末が「判断／決定／選定／確定」+「する」で結ばれる
        # (a)動詞集合と(b)動詞集合の共通要素（選定・確定）が単独で出現する場合、
        # 同一語で両条件を同時に満たすため単独出現も検出対象とする
        # （代替節による許容: `(選定|確定)する`）。
        # 条件(a)と(b)の間隔`{0,15}`は
        # 「実装時にあらためて内容を精査したうえで最終的に確定する」のような
        # 助詞・副詞1つ挿入のパターンをカバーするため15文字とする。
        # 「実装時に`agent-toolkit-edit`スキルを呼び出す」等の現在形の実施義務文は
        # (a)動詞集合のいずれとも合致しないため対象外となる。
        # 「実装時にレビュー内容を確認して最終的に決定する」等の条件(a)不成立文も
        # (a)動詞集合が現れないため対象外となる。
        re.compile(
            r"実装(時|段階)[に]?[^。\n]{0,15}?"
            r"((精査|評価|検討)[^。\n]{0,50}?(判断|決定|選定|確定)|(選定|確定))する"
        ),
    ),
    (
        "fabricated-metrics",
        re.compile(
            r"(約|およそ)?\d{1,3}\s*%[^、。\n]{0,10}(消費|使用|埋)"
            r"|残り[^、。\n]{0,10}\d[\d,]*\s*(トークン|token)"
            r"|経過\s*\d+\s*(時間|分)"
            r"|約\s*\d+[kK]?トークン"
            r"|\d+時間(経過|相当)"
            r"|\d+分(経過|相当)"
            r"|コンテキスト消費(量)?(を)?(踏まえ|考慮)"
            r"|ここまでの消費(を)?(踏まえ|考慮)"
        ),
    ),
)


# scope-escalation違反検出時に代替表現例をエラーメッセージへ添える辞書。
# 各カテゴリごとに最大2件の代替表現例を保持する。
# 代替表現の意図: 縮退・先送りに帰結する発話ではなく、規範に即した観測事象の記述・完遂宣言・
# 根本対応の提示へ書き直すよう誘導する。
_SCOPE_ESCALATION_ALTERNATIVES: dict[str, tuple[str, ...]] = {
    "workload": ("観測可能な技術的制約を根拠に述べる", "同一セッション内で完遂する方針を述べる"),
    "single-session": ("同一セッション内で完遂する方針を述べる", "計画分割の対象は同一セッション内に限定する"),
    "approach-confirm": ("技術的最適案を第1選択肢に置いた選択肢を提示する", "自律実行できる範囲は自律決定する"),
    "split-execution": ("同一セッション内での複数計画ファイル併用として提示する", "並列サブエージェント委譲として提示する"),
    "context-shortage": ("公式仕様に基づく制約のみを根拠に述べる", "自己推定を根拠とした打診を発行しない"),
    "defer-onset": ("同一計画内で対処項目として組み込む", "根本原因への対応方針を提示する"),
    "priority-consult": ("技術的最適順序を第1提案として提示する", "自律判断で順序を決めて着手する"),
    "scope-volume": ("並列サブエージェント委譲による分担案を提示する", "自律実行で完遂する方針を述べる"),
    "pattern-conformance": ("既存違反も同一計画内で是正対象として組み込む", "根本対応案を主提案として提示する"),
    "process-omission": ("各工程の実施義務を果たす", "各工程は実施対象として扱う"),
    "process-scale": ("工程数に依らず全工程を実施する", "規範上の必須工程は完遂対象として扱う"),
    "mitigation-in-adoption": ("原文どおり採用するか不採用とする二択", "反映内容の縮小は不採用根拠にならない"),
    "async-wait": ("進捗中間報告・状態確認・別作業への切替で動作を継続する",),
    "quality-gate-count": (
        "品質ゲートのブロックを正常動作として扱い1件ずつ修正して再試行する方針を述べる",
        "ブロック回数・違反件数・修正量を遂行可能性の判断材料にしない方針を述べる",
    ),
    "quality-tradeoff": ("観測可能な技術的不成立の根拠を述べる", "同一計画内で完遂する方針を述べる"),
    "next-cycle-defer": ("同一計画内で対処項目として組み込む", "同一セッション内で完遂する"),
    "plan-deferral-onset": (
        "確定的な実施文（現在形の実施義務文）で記述する",
        "実装段階での観測記録は`## 進捗ログ`側へ配置する",
    ),
    "fabricated-metrics": ("実測値を扱わず、定性的な進捗記述に留める", "実施済み工程・残工程・観測事象で進捗を述べる"),
}


# 地の文中の選択肢提示（番号付きリスト形式）を検出する補助パターン。
# Stop経路の拡張照合カテゴリ有効時に、正規表現辞書のいずれにも該当しない
# 「地の文でユーザーへ選択を委ねる」表出を`approach-confirm`として拾い上げるために用いる。
# 半角・全角の1〜9で始まる番号付き選択肢を対象とする。
_INLINE_CHOICE_PATTERN: re.Pattern[str] = re.compile(r"選択肢\s*[:：]\s*\n?\s*[1-9１-９][\.．、\)]", re.MULTILINE)


def has_inline_choice_offer(text: str) -> bool:
    """テキストへ地の文の番号付き選択肢提示が含まれる場合に真を返す。

    非文字列入力・空文字列は`False`を返す。
    """
    if not isinstance(text, str) or not text:
        return False
    return _INLINE_CHOICE_PATTERN.search(text) is not None


def _match_scope_escalation(
    text: str,
    categories: Iterable[str] | None = None,
    *,
    exclude_categories: Iterable[str] | None = None,
) -> str | None:
    """テキストへ`_SCOPE_ESCALATION_PHRASES`を照合し、最初に一致したカテゴリ識別子を返す。

    `categories`を指定した場合は当該カテゴリ集合に含まれる分類のみを照合対象とする。
    未指定時は全カテゴリを対象とする。
    `exclude_categories`を指定した場合は当該カテゴリ集合を照合対象から除外する。
    Stop経路（`stop_advisor.py`）は自由文脈での誤検出回避のため
    `_STOP_FOCUS_CATEGORIES`（`process-omission`単独）を渡す。
    未検出時・非文字列入力時はNoneを返す。
    """
    if not isinstance(text, str) or not text:
        return None
    allowed = frozenset(categories) if categories is not None else None
    excluded = frozenset(exclude_categories) if exclude_categories is not None else frozenset()
    for category, pattern in _SCOPE_ESCALATION_PHRASES:
        if allowed is not None and category not in allowed:
            continue
        if category in excluded:
            continue
        if pattern.search(text) is not None:
            return category
    return None

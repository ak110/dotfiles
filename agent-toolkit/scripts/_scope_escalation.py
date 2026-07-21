"""Claude Code agent-toolkit: scope-escalation検出用の共有辞書とマッチャー。

`pretooluse.py`と`stop_advisor.py`の双方から参照する共有モジュール。
エントリポイントスクリプト間で直接importする構造を避けるため、
`_SCOPE_ESCALATION_PHRASES`・`_match_scope_escalation`・`_SCOPE_ESCALATION_ALTERNATIVES`・
`_apply_category_exclusions`・`_ASYNC_WAIT_SELF_LAUNCHED_RE`を本モジュールへ集約する。

カテゴリ定義および代表フレーズの詳細は
`agent-toolkit/skills/agent-standards/references/scope-escalation-phrases.md`
の隔離リファレンスを参照する。

本モジュールは軽量な依存のみで動作するため、
PEP 723 script headerも重量級依存も持たない。
"""

from __future__ import annotations

import re
import sys
from collections.abc import Iterable

# Stop経路（`stop_advisor.py`）の基本照合カテゴリ集合。
# 自由文脈の誤検出リスクが低い宣言型のみに限定する。
# スキル実行中は`_STOP_FOCUS_CATEGORIES_EXTENDED`へ切り替えて照合対象を拡張する。
# `pretooluse.py`側のWrite/Edit対象文書検査は全カテゴリを対象とし、本フィルタは適用しない。
_STOP_FOCUS_CATEGORIES: frozenset[str] = frozenset({"process-omission"})

# スキル実行中（plan-mode・process-feedbacks等の起動フラグ成立時）に用いる拡張照合カテゴリ集合。
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
        "subagent-hesitation",
        "overhead-tradeoff",
    }
)

# scope-escalation縮退誘発フレーズ検出パターン。
# 01-agent.md「完遂原則」項および「縮退表明は発行しない」項目で禁止される、
# 作業量・残コンテキスト・所要時間・修正コスト等を根拠としたユーザーへの打診、
# および規範違反を明示認識せず工程を省略・割愛する宣言を機械検出する。
#
# 自身の配下でbackground起動したレビュアー系サブエージェント
# （`plan-reviewer`・`plan-codex-delegate`・`plan-impl-reviewer`等）への待機表明を検出する共有定数。
# `subagent_stop_advisor.py`の`_SELF_LAUNCHED_SUBAGENT_WAIT_RE`はbypass無効化判定に本定数のaliasを用いる
# （`from _scope_escalation import _ASYNC_WAIT_SELF_LAUNCHED_RE`）。検出と判定predicateが独立複製構造だと
# 同期漏れでSSOT不一致が発生するため、他モジュールから判定predicateとして参照される
# 検出パターン全般は本方式（共有定数として抽出しaliasで参照する）に従う。
_ASYNC_WAIT_SELF_LAUNCHED_RE = re.compile(
    r"(?i:(?:plan-reviewer|plan-codex-delegate|plan-impl-reviewer)[^,.\n]{0,40}"
    r"(?:background|waiting|running|completion notification)"
    r"|review subagents? (?:are|is) running in the background"
    r"|(?:wait|waiting) for[^,.\n]{0,30}background[^,.\n]{0,30}reviewers?)"
)

# scope-escalation縮退誘発フレーズ検出パターン。
# 01-agent.md「完遂原則」項および「縮退表明は発行しない」項目で禁止される、
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
            r"|\d+件を1(計画|セッション)で完遂は(難し|困難)"
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
            r"|観測事象として[^、。\n]{0,10}(記録|残)"
            r"|(実施|起動)[はがを]?不要(と)?判断)"
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
            r"|サブエージェント[^、。\n]{0,10}(終了|完了|応答|通知)[^、。\n]{0,10}(待つ|待機|待って)"
            r"|(?i:wait(?:ing)? for[^,.\n]{0,80}(background|parallel|subagent|reviewer"
            r"|response|report|registration|completion|notification))"
            r"|(?i:background agents[^,.\n]{0,40}(complete|finish|end)))"
        ),
    ),
    (
        "async-wait",
        re.compile(r"(?:バックグラウンド(?:実行|ジョブ|プロセス)?)[^、。\n]{0,15}(?:追跡中|継続中|実行中|進行中)"),
    ),
    (
        "async-wait",
        re.compile(r"(?:完了通知|完了報告)[^、。\n]{0,15}(?:受領|受信|到達|着信)[^、。\n]{0,15}(?:確定|続行|着手|進行|反映)"),
    ),
    (
        "async-wait",
        re.compile(r"(?:gh run watch|gh run view)[^、。\n]{0,20}(?:待機|追跡|バックグラウンド)"),
    ),
    (
        "async-wait",
        re.compile(
            r"(?i:wait(?:ing)? for (?:the )?(?:automatic )?completion notification)"
            r"|(?i:(?:rather than|instead of) (?:continue|continuing) polling)"
            r"|(?i:still running[^\n]{0,40}(?:wait|notification))"
        ),
    ),
    (
        "async-wait",
        re.compile(
            r"(?:完了通知|完了報告)(?:は)?[^、。\n]{0,15}(?:まだ)?[^、。\n]{0,5}"
            r"(?:届いていない|受領していない|到達していない|受け取っていない)"
        ),
    ),
    (
        "async-wait",
        re.compile(r"待機(?:を)?(?:継続|続行|続け)(?:する|します)?|待機継続"),
    ),
    (
        "async-wait",
        re.compile(
            r"(?:配下|下流|下位|子)(?:の)?(?:サブエージェント|エージェント|タスク|plan-implementer)"
            r"[^、。\n]{0,20}(?:再委譲|委譲|起動)[^、。\n]{0,30}(?:継続|進行|実行|作業)中"
            r"|(?:background|バックグラウンド)[^、。\n]{0,15}(?:再委譲|委譲|起動)[^、。\n]{0,20}"
            r"(?:継続|進行|完了報告受領後)"
        ),
    ),
    # 自身の配下でbackground起動したレビュアー系サブエージェントへの待機表明パターン（fb 20260720-035611-001）。
    # `has_pending_background_launches`によるbypassは自身が配下起動したサブエージェントへの
    # 待機表明も誤って通過させるため、本エントリで検出しbypass無効化判定へ用いる
    # （モジュール冒頭で定義した共有定数`_ASYNC_WAIT_SELF_LAUNCHED_RE`を参照する）。
    (
        "async-wait",
        _ASYNC_WAIT_SELF_LAUNCHED_RE,
    ),
    # 分離形の待機表明パターン（fb3反映）。
    # 「〜完了の通知を待つ」「〜完了を待つ」等、既存の「完了通知」「完了報告」連結形で捕捉できない
    # 動作名詞+「完了」の分離形を対象とする。
    # 肯定平叙形のみを検出するアンカー設計として次の除外を組み込む。
    # 否定助動詞（必要はない・ことはしない等）は`待つ|待機`の直後で負の先読みにより除外する。
    # 完了時制の除外として`待って`は継続待機を示す語尾（いる・ください・ほしい等）が続く場合のみ許容し、
    # 過去時制の後続（〜した・再開）と組み合わさる文は`待って`単独では捕捉しない。
    # 引用符（全角鍵括弧・バッククォート）内は`_apply_category_exclusions`で事前除去する。
    (
        "async-wait",
        re.compile(
            r"[^\n、。]{1,15}?完了(?:の通知)?を"
            r"(?:"
            r"(?:待つ|待機)"
            r"(?![^、。\n]{0,15}(?:必要は?ない|ことは?しない|わけでは?ない|べきでは?ない))"
            r"|待って(?=[^、。\n]{0,5}(?:いる|ください|欲しい|ほしい|から続行|から実施))"
            r")"
        ),
    ),
    # 文をまたぐ分離形の待機表明パターン（fb 20260720-162742-001反映）。
    # 「待機中。」宣言の直後に文をまたいで「完了通知」「完了報告」の受領表現が続く表明を検出する。
    # 直上の分離形パターンは同一文内の「完了を待つ」型のみを対象とし、
    # 本パターンは句点・読点で分離された文をまたぐ変形を補完する。
    # 完了時制除外は受領動詞の直後、「済み」「〜した」等の活用語尾が
    # 通常収まる範囲に限定し、無関係な後続節への波及を防ぐ。
    (
        "async-wait",
        re.compile(
            r"待機中(?:。|、)\s*[^\n]{0,80}?(?:完了(?:通知|報告))(?:を)?(?:受(?:け取|け止|領|信)|届く|届いた?)"
            r"(?![^、。\n]{0,10}(?:済|した))"
        ),
    ),
    # 自身配下で並列起動した複数サブエージェントの完了報告受領後の統合作業を
    # 待機表明として記述する複合パターン（fb1反映）。
    # 「並列起動」と「完了報告」の共起、「受領」または「受信」を経由した
    # 「統合」「集約」「反映」への近接を検出対象とする。
    # 「並列起動」〜「完了報告」間は12文字に制限し、無関係な別文をまたいだ誤結合を防ぐ
    # （句点をまたぐ「した。各」相当の短い接続のみを許容する）。
    # 完了時制（統合済み・統合した等）は同一節（読点・句点の手前）までを対象に負の先読みで除外し、
    # 「統合してレビューへ反映した」のような複合述語内の完了時制も誤検出しない。
    # 読点をまたいだ走査にすると「統合する予定であり、詳細は後日相談したい」のような
    # 無関係な後続節内の「した」を誤って完了時制と判定するため、読点で区切る。
    # 「完了報告」の代わりに「通知」を用いる報告文（「各エージェントの通知を受領後に統合する」等）も
    # 待機表明として扱う。
    (
        "async-wait",
        re.compile(
            r"並列起動[^\n]{0,12}?(?:完了(?:通知|報告)|通知)[^、。\n]{0,15}(?:受領|受信)[^。\n]{0,40}"
            r"(?:統合|集約|反映)(?![^、。\n]*(?:済|した))"
        ),
    ),
    # 配下並列レビュー起動を報告するのみで、以降の指摘集約・計画反映・needs_escalation判定を
    # 実施しない短い待機表明を検出する（fb 20260720-152203-001反映）。
    # 「並列レビューを起動して待機中」相当の短文が対象で、「並列起動」〜「完了報告」の
    # 複合パターン（直上のエントリ）では捕捉できない即時完結型の表明を補完する。
    # 完了時制除外は同一文（句点`。`到達まで）を範囲とし、読点をまたぐ後続節にある
    # 完遂表現も除外対象へ含める（「待機中だったが、現在は集約済み。」のような
    # 読点区切りの完遂表現の誤検出を防ぐため）。直上の複合パターンは要素直後の
    # 完了時制のみを除外対象とするが、本パターンは「起動して待機中」の直後に
    # 完遂節が続く広い範囲を扱うため範囲設計を独自化する。
    # 直後の1文（句点1回のまたぎまで）で完了通知・完了報告を受領済みである場合も、
    # 完了済みの報告として除外する。句点2回以上先の無関係な話題への言及まで
    # 除外対象へ含めると本来検出すべき短表明を取りこぼすため、
    # 句点のまたぎ回数を1回に制限し走査範囲を直後の1文相当へ限定する。
    (
        "async-wait",
        re.compile(
            r"(?:並列|配下|下位)[^、。\n]{0,15}(?:レビュー|サブエージェント|ジョブ|エージェント)"
            r"[^、。\n]{0,10}(?:を)?起動(?:して|し)[^、。\n]{0,10}待機中"
            r"(?![^。\n]{0,50}(?:済|した|反映した|集約した|完了した))"
            r"(?![^。\n]{0,20}(?:。[^。\n]{0,25})?完了(?:通知|報告)[^、。\n]{0,15}(?:受領|受信)(?:済み?|した))"
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
            r"|(極めて|著しく)?大規模になる"
            r"|規模[^、。\n]{0,20}(大規模|過大))"
        ),
    ),
    (
        "next-cycle-defer",
        re.compile(
            r"((次(の|回)?(サイクル|セッション|計画|ラウンド)|別セッション|独立(の)?セッション)"
            r"(で|に|へ)?(扱う|再評価|再検討|対応|送り|持ち越|引き継ぐ|回す)"
            r"|(スコープ|テーマ|計画)を超える"
            r"|今回(の)?(スコープ|対応|対象)(外|から外)"
            r"|(影響(が)?(大き|大)い|影響大)(ため|のため|により)"
            r"|現行アーキテクチャ(の)?(大幅|根本)(な?)(見直し|改修)"
            r"|後続(作業|対応|PR|チケット|issue)(で|に|へ)?(委ね|扱う|対応|送)"
            r"|後続[^。\n]{0,6}(対象外|除外)"
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
        # (a)動詞集合と(b)動詞集合の共通要素（選定・確定・決定）が単独で出現する場合、
        # 同一語で両条件を同時に満たすため単独出現も検出対象とする
        # （代替節による許容: `(選定|確定|決定)する`）。
        # 条件(a)と(b)の間隔`{0,15}`は
        # 「実装時にあらためて内容を精査したうえで最終的に確定する」のような
        # 助詞・副詞1つ挿入のパターンをカバーするため15文字とする。
        # 「実装時に`agent-toolkit-edit`スキルを呼び出す」等の現在形の実施義務文は
        # (a)動詞集合のいずれとも合致しないため対象外となる。
        # 「実装時にレビュー内容を確認して最終的に決定する」等の条件(a)不成立文も
        # (a)動詞集合が現れないため対象外となる。
        re.compile(
            r"実装(時|段階)[に]?[^。\n]{0,15}?"
            r"((精査|評価|検討)[^。\n]{0,50}?(判断|決定|選定|確定)|(選定|確定|決定))する"
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
            r"|技術的成立性(が不透明|への疑念|が疑わしい)"
            r"|これ以上進むとコンテキストが尽きる"
            r"|コンテキスト残量が観測できない"
            r"|auto compaction(を信頼|でカバー)"
            r"|観測(可能な)?範囲では(成立性が疑わしい|技術的に困難)"
            r"|(私の判断|実行)能力の限界を(感じ|認識)"
            r"|無限修正ループを避けるため"
            r"|技術判断として.*停止"
        ),
    ),
    (
        "subagent-hesitation",
        re.compile(
            r"サブエージェント(を使うか判断が迷う|活用の可否をユーザーへ確認)"
            r"|委譲(すべきか確認したい|するか判断保留)"
            r"|メインで処理するかサブエージェント委譲するか判断保留"
        ),
    ),
    (
        "overhead-tradeoff",
        re.compile(
            r"オーバーヘッド[^、。\n]{0,10}(上回|超え|見合わ)"
            r"|複雑度[^、。\n]{0,15}(高くなり過ぎない範囲|によっては[^、。\n]{0,15}(併用|対応)してよい)"
            r"|実装(コスト|複雑度)[^、。\n]{0,15}(大きい|高い)ため"
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
    "subagent-hesitation": (
        "サブエージェント委譲の可否・委譲先・並列度は技術判断として自律決定する",
        "委譲先・分割方針を確定したうえで着手する",
    ),
    "overhead-tradeoff": (
        "実装コスト・複雑度を判断材料にせず技術的最適案を選ぶ方針を述べる",
        "オーバーヘッドの多寡によらず正規手順で完遂する方針を述べる",
    ),
}


# 地の文中の選択肢提示（番号付きリスト形式）を検出する補助パターン。
# Stop経路の拡張照合カテゴリ有効時に、正規表現辞書のいずれにも該当しない
# 「地の文でユーザーへ選択を委ねる」表出を`approach-confirm`として拾い上げるために用いる。
# 半角・全角の1〜9で始まる番号付き選択肢を対象とする。
_INLINE_CHOICE_PATTERN: re.Pattern[str] = re.compile(r"選択肢\s*[:：]\s*\n?\s*[1-9１-９][\.．、\)]", re.MULTILINE)


# priority-consultカテゴリの照合対象から他ファイル節名の引用文脈を除去するためのパターン。
# 節名転記時の全角鍵括弧「」区間はpriority-consult語彙と字面上重なりやすく、過検出を招くため除外する。
_ZENKAKU_KAKKO_RE: re.Pattern[str] = re.compile(r"「[^」]*」")

# priority-consultカテゴリの照合対象から他ファイル節名の引用文脈（バッククォート囲み）を除去するためのパターン。
# バッククォート囲みは識別子・節名・コマンド名の引用に用いられ、priority-consult語彙と字面上重なりやすい。
# 全角鍵括弧と同格の除外対象として扱う。改行を含まない同一行内の囲み区間を対象とする。
_BACKTICK_RE: re.Pattern[str] = re.compile(r"`[^`\n]+`")

# async-waitカテゴリの照合対象からMarkdown引用ブロック（`>`始まりの行）を除去するためのパターン。
# 調査・報告委譲プロンプトの完了報告本文が既存設計文書の記述をMarkdown引用形式で転記する場合、
# 全角鍵括弧・バッククォート囲みのいずれにも該当せず過検出を招く事象を予防する。
_MARKDOWN_BLOCKQUOTE_RE: re.Pattern[str] = re.compile(r"^>.*$", re.MULTILINE)


def has_inline_choice_offer(text: str) -> bool:
    """テキストへ地の文の番号付き選択肢提示が含まれる場合に真を返す。

    非文字列入力・空文字列は`False`を返す。
    """
    if not isinstance(text, str) or not text:
        return False
    return _INLINE_CHOICE_PATTERN.search(text) is not None


# サブエージェント完了報告本文がSkill呼び出し単独記述のみで構成される場合を検出するパターン。
# 括弧付き引数形式（`Skill(...)`）またはコロン後に1個の非空白トークン形式（`Skill: <name>`）に限定し、
# Skill呼び出しの後に完了本文が続く正常報告を除外する。
_SKILL_INVOCATION_FULL_PATTERN: re.Pattern[str] = re.compile(r"\ASkill\s*(?:\([^()\n]*\)|:\s*\S+)\s*\Z", re.DOTALL)


def is_empty_completion_report(text: object) -> bool:
    """サブエージェント完了報告が実質空、またはSkill呼び出し単独記述か判定する。

    非文字列は`False`、trim後長さゼロは`True`、
    trim後全体が`_SKILL_INVOCATION_FULL_PATTERN`に一致する場合は`True`を返す。
    正常な短文報告（「指摘なし」等）の誤ブロックを避けるため長さ基準は採用しない。
    """
    if not isinstance(text, str):
        return False
    stripped = text.strip()
    if not stripped:
        return True
    return _SKILL_INVOCATION_FULL_PATTERN.fullmatch(stripped) is not None


def _apply_category_exclusions(text: str, category: str) -> str:
    """カテゴリ別の照合対象除外を適用する共有関数。

    現状は該当カテゴリで引用文脈（全角鍵括弧・バッククォート囲みの各区間）を除外する。
    他ファイル節名・識別子・コマンド名の引用文脈を該当語彙の過検出から保護する。
    async-waitカテゴリは分離形パターンでの引用文（「〜完了を待つ」等の指摘引用）過検出を保護する。
    async-waitカテゴリはMarkdown引用ブロック（`>`行）内の引用も同様に保護する。
    他カテゴリは呼び出し元のtextをそのまま返す。
    `_match_scope_escalation`(本モジュール)と
    `_match_scope_escalation_increase`(`pretooluse.py`)の両経路から呼び出す。
    """
    if category == "async-wait":
        return _MARKDOWN_BLOCKQUOTE_RE.sub("", _BACKTICK_RE.sub("", _ZENKAKU_KAKKO_RE.sub("", text)))
    if category == "priority-consult":
        return _BACKTICK_RE.sub("", _ZENKAKU_KAKKO_RE.sub("", text))
    return text


def _match_scope_escalation(
    text: str,
    categories: Iterable[str] | None = None,
    *,
    exclude_categories: Iterable[str] | None = None,
) -> tuple[str, str] | None:
    """テキストへ`_SCOPE_ESCALATION_PHRASES`を照合し、最初に一致した`(category, matched_phrase)`を返す。

    `categories`を指定した場合は当該カテゴリ集合に含まれる分類のみを照合対象とする。
    未指定時は全カテゴリを対象とする。
    `exclude_categories`を指定した場合は当該カテゴリ集合を照合対象から除外する。
    Stop経路（`stop_advisor.py`）は自由文脈での誤検出回避のため
    `_STOP_FOCUS_CATEGORIES`（`process-omission`単独）を渡す。
    priority-consultカテゴリは他ファイル節名・識別子の引用文脈
    （全角鍵括弧「」で囲まれた区間・バッククォート`で囲まれた区間）を
    走査対象から除去してから判定する（節名転記時の過検出を回避する）。
    matched_phraseはパターンのマッチテキストそのまま。
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
        target_text = _apply_category_exclusions(text, category)
        m = pattern.search(target_text)
        if m is not None:
            return (category, m.group(0))
    return None


def _cli_main(argv: list[str]) -> int:
    """事前検証用CLIエントリポイント。

    ブロック対象になり得る候補文言を発行前に自主検証するためのCLI。
    stdinから対象テキストを読み込み、`_SCOPE_ESCALATION_PHRASES`のいずれかに
    一致した場合はカテゴリ識別子を1行で標準出力へ出力しexit 2で終了する。
    未一致時は何も出力せずexit 0で終了する。

    使用例:

        echo "<候補文言>" | python _scope_escalation.py

    `pretooluse.py`のAskUserQuestion / Write / Editブロック案内文から
    参照される。呼び出し側は本CLIをローカル実行で起動してカテゴリを事前確認できる。
    CLI互換性のためstdout出力はカテゴリ識別子のみとし、マッチ文言は出力しない。
    """
    del argv
    text = sys.stdin.read()
    match_result = _match_scope_escalation(text)
    if match_result is None:
        return 0
    category, _matched = match_result
    print(category)
    return 2


if __name__ == "__main__":
    sys.exit(_cli_main(sys.argv[1:]))

---
name: bugfix
user-invocable: false
description: >
  問題の診断、原因分析、類似箇所の見直し、対策や再発防止策の選定を行う前に起動する。
  発見した工程（振り返り、作業項目の起草、規範改訂、レビューなど）を問わず使用する。
  不具合の有無・原因・修正方法を説明したり確認で提案したりする前にも起動する（対処を委譲先や後続の作業項目へ任せる場合も含む）。
---

# バグ対応

観測された不具合は、本来あるべき仕様（期待契約）と直接の原因を確定してから修正し、同じ原因による別の問題の発生も防ぐ。
未対応（ユーザーの要求を満たさないまま完了した項目）やデグレード（過去に動作していた機能や定めた規範の喪失）も、不具合と同じ初動手順で扱う。
本スキルは、原因分析、類似箇所の見直し、対策および再発防止策の定義を定めたものである。呼び出し元の各工程の文書では起動の契機、入力、権限、結果の適用、記録を定め、分析手順や処置の妥当性判断は本スキルを参照する。
ユーザーの発話が新規の要求や提案の形式であっても、実際の作業内容が本スキルの対象に該当する場合は、作業着手前に本スキルを適用する。単純な改善提案は不具合の対象外として区別する。
実装や文章の品質は`agent-basics:writing-standards`、レビューの判断基準は`agent-basics:review-standards`に従う。

原因を確定するには仕様や契約、実装、記録を確認する手段が必要であり、再現や修正には対象コードを実行・編集できる権限や環境が必要である。提示された資料の読み取りしかできない環境では、確認が取れた原因と未確認の候補を区別し、調査条件や修正案の下書きを返す。追加情報の取得、再現、実際の修正は、それらの操作を実行できる環境で行う。作成した下書きは[文章の品質基準](references/quality-standards.md)に沿って確認する。

QCD・3段判定、事実と推論、硬い制約・努力目標の定義は、`agent-basics:writing-standards`の`references/quality-principles.md`「QCDと3段判定」「事実と推論」「規定の区分と標示」に従う。

## 初動の判断

調査の深度や重大度にかかわらず、原因分析の記録と確定には`references/analysis-records.md`の確認完了基準を適用する。

修正や他担当への引き継ぎを行う前に、次の順序で判断する。詳細な定義は`references/root-cause-analysis.md`に集約している。

| 判断 | 確定すること | 必要な結果 |
| --- | --- | --- |
| 期待と実際 | 期待される契約、実際の観測結果、発生条件と対象バージョン | 情報の出所と観測範囲を明確にし、未確認事項を区別する |
| 原因 | 直接的な原因、作り込み・見逃しの要因、根本原因 | 裏付けとなる根拠を取得する。原因が特定できない場合は候補を絞り込む条件を記録する |
| Q影響と重大度 | エンドユーザーや利用システムへの影響、波及範囲、回復可能性 | 品質（Q）への影響とコスト・納期（C・D）だけの問題を区別し、判断の根拠を残す |
| 探索と処置 | 原因から導き出す調査対象の母集団、影響に応じた調査・対策・再発防止策 | 重大な問題は原因特定、全対象の類似見直し、再発防止策の適用まで完了する。軽微な問題は調査範囲とその限界を明確に示す |

認可の境界やユーザーが指定した結果は維持する。要求された結果を変更する処置や認可を要する操作は、事前に合意を得てから行う。実行不能、権限不足、または根拠を取得できない場合は、目標未達の理由と区別して報告する。

## 名指し参照の読込方法

本文で名指しした資料と読込表の資料は、条件が成立したらその判断や操作の前に自分で全文読み、指定箇所に注目してから、その基準で判断する。読込表の列の使い方は`agent-basics:writing-standards`の`references/quality-principles.md`「読込表の読み方」に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../writing-standards/SKILL.md` | 判断 | `read:agent-basics:*/skills/writing-standards/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../review-standards/SKILL.md` | 判断 | `read:agent-basics:*/skills/review-standards/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `references/quality-standards.md` | 判断 | `read:agent-basics:*/skills/bugfix/references/quality-standards.md` |

## 読込表

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 | 資料選択子 |
| --- | --- | --- | --- | --- |
| QCD・3段判定、事実と推論、硬い制約・努力目標を判断へ用いる前 | `agent-basics:writing-standards/references/quality-principles.md`の「QCDと3段判定」「事実と推論」「規定の区分と標示」 | 判断 | `section:agent-basics:*/skills/writing-standards/references/quality-principles.md#「QCDと3段判定」「事実と推論」「規定の区分と標示」` | |
| 期待・実際と直接的原因を確定する前（他担当へ引き渡す場合も含む） | `references/root-cause-analysis.md`の「問題を見つけたときの対処」「初動と拡張原因分析の判定」「直接的原因と事象時点の確定」 | 判断 | `section:agent-basics:*/skills/bugfix/references/root-cause-analysis.md#「問題を見つけたときの対処」「初動と拡張原因分析の判定」「直接的原因と事象時点の確定」` | |
| 根本原因と重大度を確定する前 | `references/root-cause-analysis.md`の「原因分析の品質確認」「QとC・Dの区別」 | 判断 | `section:agent-basics:*/skills/bugfix/references/root-cause-analysis.md#「原因分析の品質確認」「QとC・Dの区別」` | |
| 根本原因と重大度を確定する前 | `references/root-cause-scenarios.md`の「前提の再検討」 | 判断 | `section:agent-basics:*/skills/bugfix/references/root-cause-scenarios.md#「前提の再検討」` | |
| 類似見直しの範囲・観点を定め、処置の採否と完了を判断する前 | `references/root-cause-remediation.md`の「類似見直し」「処置の階層」「再発防止策」「再発防止策の必須性」「類似見直しと再発防止の完了」 | 判断 | `section:agent-basics:*/skills/bugfix/references/root-cause-remediation.md#「類似見直し」「処置の階層」「再発防止策」「再発防止策の必須性」「類似見直しと再発防止の完了」` | |
| 類似見直しの範囲・観点を定め、処置の採否と完了を判断する前 | `references/root-cause-analysis.md`の「対策の選定」「対策の影響範囲」 | 判断 | `section:agent-basics:*/skills/bugfix/references/root-cause-analysis.md#「対策の選定」「対策の影響範囲」` | |
| 同じ指摘や症状が反復した時 | `references/root-cause-scenarios.md`の「反復した指摘の原因判定」「同一症状の修正反復」 | 判断 | `section:agent-basics:*/skills/bugfix/references/root-cause-scenarios.md#「反復した指摘の原因判定」「同一症状の修正反復」` | |
| テストの失敗を調べる時 | `references/root-cause-scenarios.md`の「テストの原因調査」 | 判断 | `section:agent-basics:*/skills/bugfix/references/root-cause-scenarios.md#「テストの原因調査」` | |
| テストの失敗を調べる時 | `references/root-cause-remediation.md`の「再発防止としてのテスト」 | 判断 | `section:agent-basics:*/skills/bugfix/references/root-cause-remediation.md#「再発防止としてのテスト」` | |
| 所要の増大や不要仕事を調べる時 | `references/root-cause-scenarios.md`の「所要の原因調査」「反復する加工の問題」 | 判断 | `section:agent-basics:*/skills/bugfix/references/root-cause-scenarios.md#「所要の原因調査」「反復する加工の問題」` | |
| 外部の不具合報告、母集団の件数、作業途中の発見を扱う時 | `references/root-cause-scenarios.md`の「外部からの不具合報告」「母集団と件数を数える層」「作業途中で発見した事象」 | 判断 | `section:agent-basics:*/skills/bugfix/references/root-cause-scenarios.md#「外部からの不具合報告」「母集団と件数を数える層」「作業途中で発見した事象」` | |
| 拡張原因分析へ進む時、または原因確定に履歴・記録の探索や2系統の分析を用いる前 | `references/causal-analysis.md` | 判断 | `read:agent-basics:*/skills/bugfix/references/causal-analysis.md` | |
| 再発防止セルフチェックへの追加を選ぶ前 | `references/recurrence-self-check.md` | 判断 | `read:agent-basics:*/skills/bugfix/references/recurrence-self-check.md` | |
| 原因分析の記録を作成する前 | `references/analysis-records.md` | 判断 | `read:agent-basics:*/skills/bugfix/references/analysis-records.md` | |
| 独立した調査を依頼・検収する前 | `references/investigation-delegation.md` | 判断 | `read:agent-basics:*/skills/bugfix/references/investigation-delegation.md` | |
| CI失敗の分類と対処を選ぶ前 | `references/ci-failure-handling.md` | 判断 | `read:agent-basics:*/skills/bugfix/references/ci-failure-handling.md` | |
| 分岐条件の効果を検証する前 | `agent-basics:writing-standards/references/testing.md`の「分岐条件の効果の裏付け」 | 判断 | `section:agent-basics:*/skills/writing-standards/references/testing.md#「分岐条件の効果の裏付け」` | |

旧公開節は以下から定義元へ到達できる。新しい呼び出し元は読込表または定義元を直接参照する。

## 問題を見つけたときの対処

`references/root-cause-analysis.md`の「問題を見つけたときの対処」を適用する。

## 初動と拡張原因分析の判定

`references/root-cause-analysis.md`の「初動と拡張原因分析の判定」を適用する。

## 直接的原因と事象時点の確定

`references/root-cause-analysis.md`の「直接的原因と事象時点の確定」を適用する。

## 原因分析の品質確認

`references/root-cause-analysis.md`の「原因分析の品質確認」を適用する。

## QとC・Dの区別

`references/root-cause-analysis.md`の「QとC・Dの区別」を適用する。

## 再発防止策の必須性

`references/root-cause-remediation.md`の「再発防止策の必須性」を適用する。

## 対策の選定

`references/root-cause-analysis.md`の「対策の選定」を適用する。原因仮説に基づく改善の採否は、原因を事実として断定する条件と分ける。

## テストの原因調査

`references/root-cause-scenarios.md`の「テストの原因調査」を適用する。

## 所要の原因調査

`references/root-cause-scenarios.md`の「所要の原因調査」を適用する。

## 対策の影響範囲

`references/root-cause-analysis.md`の「対策の影響範囲」を適用する。

## 母集団と件数を数える層

`references/root-cause-scenarios.md`の「母集団と件数を数える層」を適用する。

## 外部からの不具合報告

`references/root-cause-scenarios.md`の「外部からの不具合報告」を適用する。

## 作業途中で発見した事象

`references/root-cause-scenarios.md`の「作業途中で発見した事象」を適用する。

## 同一症状の修正反復

`references/root-cause-scenarios.md`の「同一症状の修正反復」を適用する。

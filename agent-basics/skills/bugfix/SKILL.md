---
name: bugfix
user-invocable: false
description: >
  問題の診断・原因分析・類似見直し・対策や再発防止の選定を行う前に起動する。
  発見工程を問わず、振り返り・WI起草・規範改訂・レビューを含む。
  不具合の有無・原因・直し方を述べるか確認で提案する前にも起動する（対処を委譲先やAWIへ委ねる場合を含む）。
---

# バグ対応

観測した不具合を、期待する契約と直接的原因を確定してから是正し、同じ原因による別の問題まで防ぐ。
未対応（ユーザー要求を満たさずに完了したもの）とデグレード（過去に成立した機能・規範の喪失）も不具合として同じ初動から扱う。
本スキルは原因分析・類似見直し・対策と再発防止の定義を所有する。呼び出す工程の文書は起動契機・入力・権限・結果の適用・記録を定め、分析方法と処置の合否は本スキルを参照する。
発話が新規要求や提案の形でも、行う作業が本スキルの対象ならその前に本スキルを適用する（単純な改善提案まで一律に不具合と扱う趣旨とは異なる）。
実装と文章の品質は`agent-basics:writing-standards`、レビューの判断は`agent-basics:review-standards`へ接続する。

原因の確定には契約・実装・記録を読む手段、再現と是正には対象を実行・編集できる機能が必要である。提示された資料だけを読める環境では、裏付けられる原因と未確認の候補を分け、調査条件と是正案の下書きを返す。追加の取得・再現・修正は、それらの機能を提供する実行環境で行う。下書きは[文章の品質基準](references/quality-standards.md)で確かめる。

QCD・3段判定、事実と推論、硬い制約・努力目標は`agent-basics:writing-standards`の`references/quality-principles.md`「QCDと3段判定」「事実と推論」「規定の区分と標示」が定義する。

## 初動の判断

処置や引継ぎの前に、次の順で判断する。詳細の定義は`references/root-cause-analysis.md`へ一元化する。

| 判断 | 確定すること | 必要な結果 |
| --- | --- | --- |
| 期待と実際 | 期待契約、観測結果、発生条件と版 | 出所と観測範囲を保ち、未確認を分ける |
| 原因 | 直接的原因、作り込み・見逃し要因、根本原因 | 根拠を取得し、原因が未確定なら候補を分ける条件を残す |
| Q影響と重大度 | 消費主体への影響、波及、回復可能性 | Qへの影響と費用だけの問題を区別し、判断根拠を残す |
| 探索と処置 | 原因から導く母集団、影響に比例した探索・対策・再発防止 | 重大な問題は原因確定・全対象の類似見直し・再発防止を完了し、軽微な問題は範囲と限界を示す |

認可の境界とユーザーが選んだ結果は維持する。要求された結果を変える処置と認可を要する操作は合意後に行い、実行不能・権限不足・取得できない根拠は未達と分けて返す。

## 名指し参照の読込方法

本文の条件が成立したら、その判断の前に指定範囲を読む。読了済みで内容が変わっていない資料は再利用する。

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
| 根本原因と重大度を確定する前 | `references/root-cause-analysis.md`の「原因分析の品質確認」「QとC・Dの区別」「前提の再検討」 | 判断 | `section:agent-basics:*/skills/bugfix/references/root-cause-analysis.md#「原因分析の品質確認」「QとC・Dの区別」「前提の再検討」` | |
| 類似見直しの範囲・観点を定め、処置の採否と完了を判断する前 | `references/root-cause-analysis.md`の「類似見直し」「処置の階層」「再発防止策」「再発防止策の必須性」「対策の選定」「対策の影響範囲」「類似見直しと再発防止の完了」 | 判断 | `section:agent-basics:*/skills/bugfix/references/root-cause-analysis.md#「類似見直し」「処置の階層」「再発防止策」「再発防止策の必須性」「対策の選定」「対策の影響範囲」「類似見直しと再発防止の完了」` | |
| 同じ指摘や症状が反復した時 | `references/root-cause-analysis.md`の「反復した指摘の原因判定」「同一症状の修正反復」 | 判断 | `section:agent-basics:*/skills/bugfix/references/root-cause-analysis.md#「反復した指摘の原因判定」「同一症状の修正反復」` | |
| テストの失敗を調べる時 | `references/root-cause-analysis.md`の「テストの原因調査」「再発防止としてのテスト」 | 判断 | `section:agent-basics:*/skills/bugfix/references/root-cause-analysis.md#「テストの原因調査」「再発防止としてのテスト」` | |
| 所要の増大や不要仕事を調べる時 | `references/root-cause-analysis.md`の「所要の原因調査」「反復する加工の問題」 | 判断 | `section:agent-basics:*/skills/bugfix/references/root-cause-analysis.md#「所要の原因調査」「反復する加工の問題」` | |
| 外部の不具合報告、母集団の件数、作業途中の発見を扱う時 | `references/root-cause-analysis.md`の「外部からの不具合報告」「母集団と件数を数える層」「作業途中で発見した事象」 | 判断 | `section:agent-basics:*/skills/bugfix/references/root-cause-analysis.md#「外部からの不具合報告」「母集団と件数を数える層」「作業途中で発見した事象」` | |
| 拡張原因分析へ進む時、または原因確定に履歴・記録の探索や2系統の分析を用いる前 | `references/causal-analysis.md` | 判断 | `read:agent-basics:*/skills/bugfix/references/causal-analysis.md` | |
| 再発防止セルフチェックへの追加を選ぶ前 | `references/recurrence-self-check.md` | 判断 | `read:agent-basics:*/skills/bugfix/references/recurrence-self-check.md` | |
| 原因分析の記録を作成する前 | `references/analysis-records.md` | 判断 | `read:agent-basics:*/skills/bugfix/references/analysis-records.md` | |
| 独立した調査を依頼・検収する前 | `references/investigation-delegation.md` | 判断 | `read:agent-basics:*/skills/bugfix/references/investigation-delegation.md` | |
| CI失敗の分類と対処を選ぶ前 | `references/ci-failure-handling.md` | 判断 | `read:agent-basics:*/skills/bugfix/references/ci-failure-handling.md` | |
| 分岐条件の効果を検証する前 | `agent-basics:writing-standards/references/testing.md`の「分岐条件の効果の裏付け」 | 判断 | `section:agent-basics:*/skills/writing-standards/references/testing.md#「分岐条件の効果の裏付け」` | |

旧公開節は以下から定義元へ到達できる。新しい呼出元は読込表または定義元を直接参照する。

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

`references/root-cause-analysis.md`の「再発防止策の必須性」を適用する。

## 対策の選定

`references/root-cause-analysis.md`の「対策の選定」を適用する。

## テストの原因調査

`references/root-cause-analysis.md`の「テストの原因調査」を適用する。

## 所要の原因調査

`references/root-cause-analysis.md`の「所要の原因調査」を適用する。

## 対策の影響範囲

`references/root-cause-analysis.md`の「対策の影響範囲」を適用する。

## 母集団と件数を数える層

`references/root-cause-analysis.md`の「母集団と件数を数える層」を適用する。

## 外部からの不具合報告

`references/root-cause-analysis.md`の「外部からの不具合報告」を適用する。

## 作業途中で発見した事象

`references/root-cause-analysis.md`の「作業途中で発見した事象」を適用する。

## 同一症状の修正反復

`references/root-cause-analysis.md`の「同一症状の修正反復」を適用する。

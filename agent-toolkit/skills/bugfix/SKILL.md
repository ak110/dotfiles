---
name: bugfix
user-invocable: false
description: >
  問題の診断・原因分析・類似見直し・対策や再発防止の選定を行う前に起動する。
  発見工程を問わず、振り返り・WI起草・規範改訂・レビューを含む。
  不具合の有無・原因・直し方を述べるか確認で提案する前にも起動する（対処を委譲先やAWIへ委ねる場合を含む）。
---

# 原因分析の品質基準と運用

原因・母集団・処置の品質基準は`agent-basics:bugfix`が定義する。本スキルはWI記録、調査委譲、CI工程、履歴とセッションの扱いを所有する。

## 名指し参照の読込方法

本文の各参照の適用条件を保ち、選んだ資料の指定範囲を取得してから、その基準を使う具体的な判断へ進む。起動時の行の資料は起動本文とともに届く。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:writing-standards/references/design-heuristics.md#「設計案の予備選別」` | 判断 | `section:agent-basics:*/skills/writing-standards/references/design-heuristics.md#「設計案の予備選別」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../process-wi/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../user-confirmation-and-report/SKILL.md#「認可を要する操作」` | 判断 | `section:agent-toolkit:*/skills/user-confirmation-and-report/SKILL.md#「認可を要する操作」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../user-confirmation-and-report/SKILL.md#「確認要否の判定」` | 判断 | `section:agent-toolkit:*/skills/user-confirmation-and-report/SKILL.md#「確認要否の判定」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../wi-standards/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../workflow-overview/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/workflow-overview/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../writing-standards/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/writing-standards/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../user-confirmation-and-report/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/user-confirmation-and-report/SKILL.md` |
| 計画・実装・レビュー・CI失敗の原因分析を始める前 | `agent-basics:bugfix/references/root-cause-analysis.md#「初動と拡張原因分析の判定」` | 判断 | `section:agent-basics:*/skills/bugfix/references/root-cause-analysis.md#「初動と拡張原因分析の判定」` |

## 読込表

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 | 資料選択子 |
| --- | --- | --- | --- | --- |
| 起動時 | `agent-basics:bugfix/SKILL.md` | 起動時 | `skill:bugfix` | |
| AWIの原因分析を起草する前、計画ファイル（バグ）を作成する前 | `references/analysis-records.md` | 判断 | `read:agent-toolkit:*/skills/bugfix/references/analysis-records.md` | |
| 事象単位の調査を委譲する前 | `references/investigation-delegation.md` | 判断 | `read:agent-toolkit:*/skills/bugfix/references/investigation-delegation.md` | |
| CI失敗を扱う前 | `references/ci-failure-handling.md` | 判断 | `read:agent-toolkit:*/skills/bugfix/references/ci-failure-handling.md` | |
| ユーザー介入・確認、規範の未適用を分析する前と、類似見直しの委譲・記録・受渡しの前 | `references/analysis-operations.md` | 判断 | `read:agent-toolkit:*/skills/bugfix/references/analysis-operations.md` | |
| 再発防止セルフチェック観点の配置・保守の前 | `references/recurrence-self-check.md` | 判断 | `read:agent-toolkit:*/skills/bugfix/references/recurrence-self-check.md` | |
| CI通過の確認へ入る前 | `agent-toolkit:commit`の`references/push-and-ci.md` | 判断 | `read:agent-toolkit:*/skills/commit/references/push-and-ci.md` | |
| 履歴を扱う実装単位へ入る前 | `${CLAUDE_PLUGIN_ROOT}/share/exec.subagent.md` | 判断 | `read:agent-toolkit:*/share/exec.subagent.md` | |
| 実装担当の委譲先を確定する前 | `agent-toolkit:delegation`を起動し、同スキルの`references/runtime-routing.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/runtime-routing.md` | |

## 問題を見つけたときの対処

対処の候補は`agent-toolkit/rules/01-agent.md`「判断指針」の3段判定で選別し、構造の比較には`agent-basics:writing-standards/references/design-heuristics.md`「設計案の予備選別」を使う。

- 既存不良も原則として同じセッションで類似見直し・再発防止策まで完遂する。先送りと担当外不良は`agent-toolkit/rules/01-agent.md`「完遂と先送り」、実行中のスキル固有の扱いはその手順（`agent-toolkit:process-wi`など）に従う。拡張原因分析・類似見直し・再発防止策のいずれかを要する調査は`${CLAUDE_PLUGIN_ROOT}/skills/bugfix/references/investigation-delegation.md`へ委譲し、修正は見つけたエージェントが返却された対策で行う。根本原因と類似見直しの母集団を自ら確定でき、その全対象と再発防止策をその場で処置できる不良はその場で直す
- 続行を妨げない不自然さ・非効率・反復は即時是正から外し、メインは`agent-toolkit/share/rules-main.md`「作業中の改善点の報告」、委譲先は`agent-toolkit/share/rules-subagent.md`「返却と終端」の`気付いた改善点:`で扱う
- 完成条件外のエンドユーザー向けの不足は`agent-toolkit:user-confirmation-and-report`の`SKILL.md`「認可を要する操作」で判定する。文書を機能の制約へ合わせた場合も欠けた機能を判定し、新しい挙動の追加は同書「確認を要する事項」へ送る
- 終了コード0でも警告は意味を確かめる。未適用・未完了を示す警告には対処し、無関係と判断した警告は根拠を報告へ残す
- ユーザーが示した要件を変える変更と、「認可を要する操作」に当たる対処は合意を得てから着手する。手段の選択は`agent-toolkit:user-confirmation-and-report`「確認要否の判定」で決め、内部実装に閉じる対処は自律的に完遂する
- フック・ツール・実行環境の通知が担当外の対象、実行できない処置、実在しない前提を示した場合は、通知への応答とは別に通知機構の欠陥として処置する（同一セッションでの是正、`agent-toolkit:wi-standards`に従うAWI投入の提案、同じ欠陥を扱う未終端WIの参照のいずれか）

## 適用手順

事象がWI処理の工程や運用に関わる場合は、対策を選ぶ前に`agent-toolkit:workflow-overview`を起動する。

計画、実装、レビュー、CI失敗のいずれでも`agent-basics:bugfix/references/root-cause-analysis.md`「初動と拡張原因分析の判定」から始め、読込表に従って参照資料へ進む。
文書・規範・出力文面を変える対策を提案または委譲する前は、`agent-toolkit:writing-standards`を起動し、受け取る主体が観測できる条件で方向を評価する。

## 暫定回避と結果報告

解除条件のある暫定回避は`agent-toolkit:wi-standards`の通常の投入手順で、正式対応の対象・解除条件・復帰後の結果を保存済みAWIへ接続する。時間経過・外部公開待ちは冷却期限を設定する。再発防止策の採否は作業完了報告と報告用UWIの`### 自ら確定した判断`へ根拠とともに記録し、ユーザーだけの値は`agent-toolkit:user-confirmation-and-report`で確認する。

## 初動と拡張原因分析の判定

品質の定義は`agent-basics:bugfix/references/root-cause-analysis.md`「初動と拡張原因分析の判定」を適用する。

## 直接的原因と事象時点の確定

品質の定義は`agent-basics:bugfix/references/root-cause-analysis.md`「直接的原因と事象時点の確定」を適用する。

## 原因分析の品質確認

品質の定義は`agent-basics:bugfix/references/root-cause-analysis.md`「原因分析の品質確認」を適用する。

## QとC・Dの区別

品質の定義は`agent-basics:bugfix/references/root-cause-analysis.md`「QとC・Dの区別」を適用する。

## 再発防止策の必須性

品質の定義は`agent-basics:bugfix/references/root-cause-analysis.md`「再発防止策の必須性」を適用する。

## 対策の選定

品質の定義は`agent-basics:bugfix/references/root-cause-analysis.md`「対策の選定」を適用する。

## テストの原因調査

品質の定義は`agent-basics:bugfix/references/root-cause-analysis.md`「テストの原因調査」を適用する。

## 所要の原因調査

品質の定義は`agent-basics:bugfix/references/root-cause-analysis.md`「所要の原因調査」を適用する。

## 対策の影響範囲

品質の定義は`agent-basics:bugfix/references/root-cause-analysis.md`「対策の影響範囲」を適用する。

## 母集団と件数を数える層

品質の定義は`agent-basics:bugfix/references/root-cause-analysis.md`「母集団と件数を数える層」を適用する。

## 外部からの不具合報告

品質の定義は`agent-basics:bugfix/references/root-cause-analysis.md`「外部からの不具合報告」を適用する。

## 作業途中で発見した事象

品質の定義は`agent-basics:bugfix/references/root-cause-analysis.md`「作業途中で発見した事象」を適用する。

## 同一症状の修正反復

品質の定義は`agent-basics:bugfix/references/root-cause-analysis.md`「同一症状の修正反復」を適用する。

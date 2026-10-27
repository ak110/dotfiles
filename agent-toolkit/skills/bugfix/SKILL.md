---
name: bugfix
user-invocable: false
description: >
  問題の診断、原因分析、類似箇所の見直し、対策や再発防止策の選定を行う前に起動する。
  発見した工程を問わず、振り返り、WI起草、規範改訂、レビューを含む。
  不具合の有無や原因、修正方法を説明する場合や、確認で提案する前にも起動する（対処を委譲先やAWIへ委ねる場合を含む）。
---

# 原因分析の品質基準と運用

原因や母集団、処置に関する品質基準は`agent-basics:bugfix`が定める。本スキルでは、WIへの記録、調査の委譲、CI工程、履歴やセッションの運用手順を扱う。

## 名指し参照の読込方法

本文の各参照で定めた適用条件に従い、選んだ資料を読み込み、指定箇所に注目してから、その基準に基づく具体的な判断へ進む。起動時の行に記載された資料は、起動時のプロンプトとともに提供される。

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

- 既存の不具合を発見した場合も、原則として同じセッション内で類似箇所の見直しや再発防止策の策定まで完遂する。先送りの判断や担当外の不具合の扱いは`agent-toolkit/rules/01-agent.md`「完遂と先送り」に従い、実行中のスキルに固有の手順（`agent-toolkit:process-wi`など）がある場合はそちらを優先する。拡張原因分析、類似箇所の見直し、再発防止策の策定のいずれかを要する調査は、`${CLAUDE_PLUGIN_ROOT}/skills/bugfix/references/investigation-delegation.md`へ委譲する。修正作業は、調査担当から返却された対策をもとに、不具合を見つけたエージェント自身が行う。根本原因と類似見直しの母集団を自力で確定でき、すべての対象と再発防止策をその場で処置できる場合は、委譲せずにその場で修正してよい
- 作業の続行を妨げない不自然な実装、非効率な処理、不要な反復などは、その場ですぐに是正せず改善点として報告する。メインエージェントは`agent-toolkit/share/rules-main.md`「作業中の改善点の報告」に従い、委譲先エージェントは`agent-toolkit/share/rules-subagent.md`「返却と終了」の`気付いた改善点:`に記載して報告する
- 完成条件に含まれないエンドユーザー向け機能の不足については、`agent-toolkit:user-confirmation-and-report`の`SKILL.md`「認可を要する操作」に基づいて要否を判定する。ドキュメントを既存機能の制約に合わせて修正した場合も不足している機能を評価し、新たな挙動を追加する場合は同スキルの「確認を要する事項」に従ってユーザーへ確認する
- 終了コードが0であっても、出力された警告の内容と意味を確認する。設定や処理の未適用・未完了を示す警告には適切に対処し、今回の作業と無関係であると判断した警告については、その判断根拠を報告に記録する
- ユーザーが指定した要件を変更する場合や、「認可を要する操作」に該当する対処をする場合は、事前にユーザーの合意を得てから着手する。確認が必要かどうかの判断は`agent-toolkit:user-confirmation-and-report`「確認要否の判定」に従う。内部実装だけで完結する対処については、確認を挟まず自律的に完遂する
- フックやツール、実行環境からの通知が担当外の対象、実行不可能な処置、あるいは実在しない前提を示していることがある。この場合は個別対応とは別に、通知機構自体の欠陥として処置する（同一セッション内での是正、`agent-toolkit:wi-standards`に従ったAWI起草の提案、または同じ欠陥を扱う未完了WIの参照のいずれかを行う）

## 適用手順

発生した事象がWI処理の工程や運用に関わる場合は、具体的な対策を選ぶ前に`agent-toolkit:workflow-overview`を起動する。

計画、実装、レビュー、CI失敗のどの場面であっても、まずは`agent-basics:bugfix/references/root-cause-analysis.md`「初動と拡張原因分析の判定」を参照し、読込表の案内に従って必要な資料を読み進める。
ドキュメント、規範、ツールの出力文面などを変更する対策を提案または委譲する際は、事前に`agent-toolkit:writing-standards`を起動し、それを受け取る主体が実際に観測できる条件に基づいて変更の方向性を評価する。

## 暫定回避と結果報告

解除条件を伴う暫定回避を行う場合は、`agent-toolkit:wi-standards`の通常の投入手順に従い、正式対応の対象、解除条件、復帰後に期待される結果を保存済みAWIへ記録して接続する。時間経過や外部公開の待機を伴う場合は、再確認までの冷却期限を設定する。再発防止策を採用するかどうかの判断は、作業完了報告および報告用UWIの`### 自ら確定した判断`に根拠とともに記録する。ユーザーのみが判断できる事項については、`agent-toolkit:user-confirmation-and-report`に従って確認する。

## 初動と拡張原因分析の判定

品質基準の詳細は、`agent-basics:bugfix/references/root-cause-analysis.md`「初動と拡張原因分析の判定」を参照して適用する。

## 直接的原因と事象時点の確定

品質基準の詳細は、`agent-basics:bugfix/references/root-cause-analysis.md`「直接的原因と事象時点の確定」を参照して適用する。

## 原因分析の品質確認

品質基準の詳細は、`agent-basics:bugfix/references/root-cause-analysis.md`「原因分析の品質確認」を参照して適用する。

## QとC・Dの区別

品質基準の詳細は、`agent-basics:bugfix/references/root-cause-analysis.md`「QとC・Dの区別」を参照して適用する。

## 再発防止策の必須性

品質基準の詳細は、`agent-basics:bugfix/references/root-cause-analysis.md`「再発防止策の必須性」を参照して適用する。

## 対策の選定

品質基準の詳細は、`agent-basics:bugfix/references/root-cause-analysis.md`「対策の選定」を参照して適用する。

## テストの原因調査

品質基準の詳細は、`agent-basics:bugfix/references/root-cause-analysis.md`「テストの原因調査」を参照して適用する。

## 所要の原因調査

品質基準の詳細は、`agent-basics:bugfix/references/root-cause-analysis.md`「所要の原因調査」を参照して適用する。

## 対策の影響範囲

品質基準の詳細は、`agent-basics:bugfix/references/root-cause-analysis.md`「対策の影響範囲」を参照して適用する。

## 母集団と件数を数える層

品質基準の詳細は、`agent-basics:bugfix/references/root-cause-analysis.md`「母集団と件数を数える層」を参照して適用する。

## 外部からの不具合報告

品質基準の詳細は、`agent-basics:bugfix/references/root-cause-analysis.md`「外部からの不具合報告」を参照して適用する。

## 作業途中で発見した事象

品質基準の詳細は、`agent-basics:bugfix/references/root-cause-analysis.md`「作業途中で発見した事象」を参照して適用する。

## 同一症状の修正反復

品質基準の詳細は、`agent-basics:bugfix/references/root-cause-analysis.md`「同一症状の修正反復」を参照して適用する。

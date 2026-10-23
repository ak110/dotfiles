---
name: workflow-overview
description: >
  WI処理の工程や運用に関わる問題の原因分析と対策、運用工程や自動化の変更計画、
  ユーザーへの運用説明で起動する。キュー、`atk wi process-loop`、process-wiの1回の実行、確認と回答、振り返りで得た要求を投入する方法を扱う。
user-invocable: false
---

# WI処理の運用概要

複数のセッションとユーザーの操作にまたがるWI処理の全体像を示す。
個々の工程の実行契約は`${CLAUDE_PLUGIN_ROOT}/share/workflow-phases.md`と各工程のスキル（`agent-toolkit:plan-mode`、`agent-toolkit:process-wi`など）が定める。

運用の説明は提示された資料の範囲でも行える。キュー・process-loop・レーンの現物の照会と操作には、参照資料、対象へのアクセスとCLIが必要であり、機能が無い環境では説明と実際の設定・状態変更を区別する。運用操作はそれらの機能を使えるClaude CodeまたはCodexで行い、説明の文には[文章の品質基準](references/quality-standards.md)を使う。

## 名指し参照の読込方法

本文の各参照の適用条件を保ち、選んだ資料の指定範囲を取得してから、その基準を使う具体的な判断へ進む。起動時の行の資料は起動本文とともに届く。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../plan-mode/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/plan-mode/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../process-wi/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `references/quality-standards.md` | 判断 | `read:agent-toolkit:*/skills/workflow-overview/references/quality-standards.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../plan-mode/SKILL.md#「計画ファイルの作成要否」` | 判断 | `section:agent-toolkit:*/skills/plan-mode/SKILL.md#「計画ファイルの作成要否」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../user-confirmation-and-report/SKILL.md#「確認要否の判定」` | 判断 | `section:agent-toolkit:*/skills/user-confirmation-and-report/SKILL.md#「確認要否の判定」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../single-lane-process/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/single-lane-process/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../add-awi-by-user/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/add-awi-by-user/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../share/single-lane-execution.md` | 判断 | `read:agent-toolkit:*/share/single-lane-execution.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../session-review/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/session-review/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../wi-standards/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../user-confirmation-and-report/SKILL.md#「手段の選択」` | 判断 | `section:agent-toolkit:*/skills/user-confirmation-and-report/SKILL.md#「手段の選択」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../completion-report/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/completion-report/SKILL.md` |

## 読込表

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 | 資料選択子 |
| --- | --- | --- | --- | --- |
| 工程の責務、受渡しまたは出口を対策や説明の根拠にする前 | `${CLAUDE_PLUGIN_ROOT}/share/workflow-phases.md` | 判断 | `read:agent-toolkit:*/share/workflow-phases.md` | |
| 登録した複数リポジトリの担当起動・制御・再開を扱う前 | `../process-wi/references/all-repos-orchestration.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/references/all-repos-orchestration.md` | |
| レーンの準備・統合・回復・回収を扱う前 | `../process-wi/references/run-lanes.md`の「レーンと資源」と「統合とAWI終端」 | 判断 | `section:agent-toolkit:*/skills/process-wi/references/run-lanes.md#「レーンと資源」「統合とAWI終端」` | |

## 利用形態と起動主体

| 利用形態 | 起動主体と公開操作 | 処理の責務 |
| --- | --- | --- |
| 対話型 | ユーザーがエージェントへ直接依頼する | エージェントが依頼を扱う。計画の作成要否は`agent-toolkit:plan-mode`「計画ファイルの作成要否」、確認の要否は`agent-toolkit:user-confirmation-and-report`「確認要否の判定」で決める |
| 自律型 | ユーザーが`atk wi process-loop`を起動する | process-loopが開始前の更新・待機・子セッション起動を管理し、子の`agent-toolkit:process-wi`が対象WIの処理を担う |
| まとめ処理型 | ユーザーが`agent-toolkit:single-lane-process`を手動起動する | たまったWIを1回の実行で扱う |
| 定期実行型 | ユーザーがcronなどへ`atk run-skill`を登録する | WIキューを経由せずスキル1件を自律モードで実行し、結果は報告用UWI、判断を要する対応は事前承認型UWIで届ける |

process-loopの起動・待機・制御を扱う前に`atk wi process-loop --help`で条件を確認する。次の公開操作は、中断要求の`atk wi process-loop abort`、次の1セッションへの指示の`instruct`、状態確認の`status`である。
複数リポジトリはホームから`atk wi process-loop all-repos`で起動し、`process_loop_repositories`へ登録した集合を`atk wi list --configured-repos`で確認する。常駐側は担当の起動・中継・所有管理を担い、対象の選定・規範・実装・Git・公開の判断は対象リポジトリの担当が持つ。登録と再開の条件は`../process-wi/references/all-repos-orchestration.md`が定める。

process-wiのメインは選定・配分とユーザー確認を担い、レーン担当が計画・実装・独立した実行レビューの調整・統合を続ける。単一レーンの継続契約は`${CLAUDE_PLUGIN_ROOT}/share/single-lane-execution.md`とする。準備は`atk lane create`、統合は`atk lane integrate`、回収は`atk lane delete`へ進み、操作前に`../process-wi/references/run-lanes.md`の該当節を読む。保持された資源の回復は同書と`atk lane delete --help`・`atk lane recover --help`の条件・診断から再開する。

`run-skill`由来のUWIは`source`が`run-skill`、`scope`がスキル名であり、process-wiの選定とprocess-loopの起動件数から除く。

## 登録、回答、振り返り

要求はユーザーが`atk wi add`、`agent-toolkit:add-awi-by-user`、`atk serve`のWI画面から登録し、処理中のエージェントと`agent-toolkit:session-review`も投入する。本文、由来、状態と依存は`agent-toolkit:wi-standards`が定める。

ユーザー対応を要する未完了事項は、モードによらず共通の確認基準で個別UWIの登録要否を判定し、報告は保存した所在を示す。自明な取得・再実行・実機確認の案内は通常の報告へ添える。手順が定める完了報告用UWIは保存し、ユーザーが確認する。会話中の確認で回答を得た事項はUWIに重ねず、回答を得ないまま終わった確認はUWIへ移す。保存の条件、自律モードの確認手段とUWIへの切替は`agent-toolkit:user-confirmation-and-report`「手段の選択」に従う。ユーザーは`atk wi answer`か`atk serve`で回答する。回答済みUWIは、`source`が`run-skill`なら同じ対象リポジトリと同じスキルの次回の`atk run-skill`が、それ以外はprocess-wiの次の実行のメインが元項目とともに取り込む。処理中のprocess-wiのセッションへユーザーが回答を告げた場合は同じ実行の処理対象へ加える（`${CLAUDE_PLUGIN_ROOT}/skills/process-wi/references/selection-procedure.md`「処理対象WIの追加」）。

作業完了後は`agent-toolkit:completion-report`から`agent-toolkit:session-review`が起動し、振り返りが投入したAWIは次のprocess-loopセッションで処理される。

## 運用変更を検討する観点

別セッションのprocess-loopが並行して稼働している前提で対象と状態遷移を調べる。対策がprocess-loop、子セッションのメイン、レーン担当、まとめ処理型のセッションのどれに作用するか、各利用形態から到達できるか、ユーザーの操作が増えるかを比べる。

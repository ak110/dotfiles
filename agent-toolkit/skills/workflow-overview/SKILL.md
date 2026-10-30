---
name: workflow-overview
description: >
  WI処理の工程や運用に関わる問題の原因分析と対策、運用工程や自動化の変更計画、
  ユーザーへの運用説明で起動する。キュー、`atk wi process-loop`、process-wiの1回の実行、確認と回答、振り返りで得た要求を投入する方法を扱う。
user-invocable: false
---

# WI処理の運用概要

複数のセッションやユーザーの操作にまたがるWI処理の全体像をまとめている。
各工程の実行契約は`${CLAUDE_PLUGIN_ROOT}/share/workflow-phases.md`および各工程のスキル（`agent-toolkit:plan-mode`、`agent-toolkit:process-wi`など）を参照する。

運用の説明は、提示された資料の範囲内でも実施できる。一方でキューやprocess-loop、レーンの現物を照会・操作するには、参照資料に加えて対象へのアクセス権とCLIが必要になる。それらの機能が利用できない環境では、机上の説明と実際の設定・状態変更とを区別して扱う。運用操作は該当機能を利用できるClaude CodeまたはCodexで実行し、説明文には[文章の品質基準](references/quality-standards.md)を適用する。

## 名指し参照の読込方法

本文で名指しした資料と読込表の資料は、条件が成立したらその判断や操作の前に自分で全文読み、指定箇所に注目してから、その基準で判断する。読込表の列の使い方は`agent-basics:writing-standards`の`references/quality-principles.md`「読込表の読み方」に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../session-review-batch/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/session-review-batch/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../process-wi-all-repos/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/process-wi-all-repos/SKILL.md` |
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
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../user-confirmation-and-report/references/communication-methods.md#「手段の選択」` | 判断 | `section:agent-toolkit:*/skills/user-confirmation-and-report/references/communication-methods.md#「手段の選択」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../completion-report/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/completion-report/SKILL.md` |
| 読込表と本文の名指し参照を読む前 | `agent-basics:writing-standards/references/quality-principles.md`の「読込表の読み方」 | 判断 | `section:agent-basics:*/skills/writing-standards/references/quality-principles.md#「読込表の読み方」` |

## 読込表

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 | 資料選択子 |
| --- | --- | --- | --- | --- |
| 工程の責務、受渡しまたは出口を対策や説明の根拠にする前 | `${CLAUDE_PLUGIN_ROOT}/share/workflow-phases.md` | 判断 | `read:agent-toolkit:*/share/workflow-phases.md` | |
| 登録した複数リポジトリの担当起動・制御・再開を扱う前 | `../process-wi/references/all-repos-orchestration.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/references/all-repos-orchestration.md` | |
| レーンの準備・統合・回復・回収を扱う前 | `../process-wi/references/run-lanes.md`の「レーンと資源」と「統合とAWI終了」 | 判断 | `section:agent-toolkit:*/skills/process-wi/references/run-lanes.md#「レーンと資源」「統合とAWI終了」` | |

## 利用形態と起動主体

| 利用形態 | 起動主体と公開操作 | 処理の責務 |
| --- | --- | --- |
| 対話型 | ユーザーがエージェントへ直接依頼する | エージェントが依頼を処理する。計画の作成要否は`agent-toolkit:plan-mode`「計画ファイルの作成要否」、確認の要否は`agent-toolkit:user-confirmation-and-report`「確認要否の判定」に従って決める |
| 自律型 | ユーザーが`atk wi process-loop`を起動する | process-loopが開始前の更新・待機と、登録リポジトリ全体を処理する統括セッションの起動を管理する。統括は`agent-toolkit:process-wi-all-repos`を実行する |
| まとめ処理型 | ユーザーが`agent-toolkit:single-lane-process`を手動起動する | 蓄積されたWIを1回の実行でまとめて処理する |
| 定期実行型 | ユーザーがcronなどに`atk run-skill`を登録する | WIキューを経由せずにスキル1件を自律モードで実行し、結果は報告用UWIで、判断を要する対応は事前承認型UWIで届ける |

process-loopの起動・待機・制御を扱う際は、事前に`atk wi process-loop --help`で利用条件を確認する。公開されている主な操作には、中断を要求する`atk wi process-loop abort`、次の1セッションへの指示を与える`instruct`、状態を確認する`status`がある。
ホームディレクトリなどから`atk wi process-loop`を実行し、`process_loop_repositories`に登録されている対象一覧を`atk wi list --configured-repos`で確認する。
常駐はprocess-wiの実行ごとに統括担当を1つ起動し、リポジトリごとの準備・所有・進捗を管理する。
統括は対象の規範・スキルを明示的に読み、少数WIのリポジトリを自ら処理する。
既存のレーン数上限が2以上となるリポジトリだけを、対象worktreeの`agent-toolkit:process-wi`担当へ委譲する。
計画・検証・レビュー・公開は対象worktreeごとに分離し、process-wiの実行全体の完了報告は統括が行う。
常時処理するリポジトリだけを登録し、投入だけを行うリポジトリは登録しない。
非常駐の全体処理は`agent-toolkit:process-wi-all-repos`を手動起動する。同スキルは`atk wi process-loop --once`で、常駐と共通の対象取得・所有・worktree準備・結果検収・回収を1集合に対して行う。
対象が0件なら待機せず終了し、未完了・所有待ち・準備失敗が残れば非0で終了する。表示された記録を確認し、`atk wi process-loop --once --auto-resume`で再開する。
手動起動元はCLIの終了を観測して結果を返し、完了報告とUWIの保存は統括が1回行う。
対象リポジトリの単発処理は`agent-toolkit:process-wi`または`agent-toolkit:single-lane-process`を使う。
登録や再開の条件は`../process-wi/references/all-repos-orchestration.md`に従う。

process-wiのメインセッションは対象の選定・配分とユーザー確認を担当し、レーン担当が計画、実装、実行レビューの調整、統合を順次進める。初回の実行レビューでは詳細に点検し、レビュー限定基準への違反があれば指摘として通知する。再レビューでは、前回の指摘事項、修正差分、および直接影響範囲を確認する。レーン数によらない継続契約は`${CLAUDE_PLUGIN_ROOT}/share/single-lane-execution.md`に準拠し、複数レーンを運用する場合はメインセッションが1レーンずつ統合を許可する。準備は`atk lane create`、統合は`atk lane integrate`、回収は`atk lane delete`の順で進め、各操作の実行前に`../process-wi/references/run-lanes.md`の該当節を確認する。保持された資源の回復は、同書ならびに`atk lane delete --help`、`atk lane recover --help`に記載された条件や診断に従って再開する。

`run-skill`に由来するUWIは、`source`が`run-skill`、`scope`がスキル名となり、process-wiの選定対象やprocess-loopの起動件数の計算から除外する。

## 登録、回答、振り返り

要求事項の登録は、ユーザーが`atk wi add`や`agent-toolkit:add-awi-by-user`、`atk serve`のWI管理画面から行うほか、処理中のエージェントや`agent-toolkit:session-review`からも投入できる。本文の記述形式、由来、状態、依存関係の扱いは`agent-toolkit:wi-standards`に従う。

ユーザーの対応を必要とする未完了事項については、実行モードを問わず共通の確認基準に基づいて個別のUWIを登録するか判定し、報告時には保存先のパスを示す。自明な情報の取得や再実行、実機確認の案内は通常の報告に添えて伝える。手順で定められた完了報告用のUWIは適切に保存し、ユーザーの確認を受ける。会話中のやり取りで回答を得られた事項はUWIとして重ねて登録せず、回答を得られないまま終了した確認事項はUWIへ引き継ぐ。保存条件、自律モードにおける確認手段、およびUWIへの切り替え手順は`agent-toolkit:user-confirmation-and-report`「手段の選択」に従う。ユーザーは`atk wi answer`または`atk serve`から回答を入力する。回答済みのUWIについては、`source`が`run-skill`であれば同じ対象リポジトリ・同じスキルの次回`atk run-skill`実行時に取り込み、それ以外はprocess-wiの次回実行時にメインセッションが元の項目とともに処理へ取り込む。処理中のprocess-wiセッションに対してユーザーから回答が伝えられた場合は、同一セッションの処理対象に追加する（`${CLAUDE_PLUGIN_ROOT}/skills/process-wi/references/selection-procedure.md`「処理対象WIの追加」）。

作業完了後は`agent-toolkit:completion-report`で作業結果を報告して終了する。
Claude CodeとCodexの最上位セッションは振り返り待ちとして記録され、非活動になった対象を`agent-toolkit:session-review-batch`が非同期でまとめて分析する。
バッチが投入したAWIは登録時点から24時間後に処理可能となる。
手動の`agent-toolkit:session-review`は現在のセッション全体を対象とし、引数は追加の着眼点として扱う。
自動起動は`session_review_auto_start`で切り替え、無効時も待ち記録を保持して手動のbatchで処理できる。

## 運用変更を検討する観点

別のセッションでprocess-loopが並行して稼働している前提に立ち、対象リソースと状態遷移を慎重に確認する。検討中の対策がprocess-loop、子セッションのメイン、レーン担当、まとめ処理型セッションのどこに影響するかを整理し、各利用形態から到達可能か、ユーザー側の操作負荷が増加しないかを比較して評価する。

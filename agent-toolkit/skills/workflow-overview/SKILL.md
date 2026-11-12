---
name: workflow-overview
description: >
  WI処理の工程や運用に関わる問題の原因分析と対策、運用工程や自動化の変更計画、
  ユーザーへの運用説明で起動する。process-loop・run-skillの統括の挙動（起動、turn終了、再開、外からの制御）を前提に、案・回答・確認の選択肢を出す前にも起動する。
  キュー、`atk wi process-loop`、process-wiの1回の実行、確認と回答、振り返りで得た要求を投入する方法を扱う。
user-invocable: false
---

# WI処理の運用概要

複数のセッションやユーザーの操作にまたがるWI処理の全体像をまとめている。
各工程の実行契約は`${CLAUDE_PLUGIN_ROOT}/share/workflow-phases.md`および各工程のスキル（`agent-toolkit:write-plan`、`agent-toolkit:process-wi`など）を参照する。

運用の説明は、提示された資料の範囲内でも実施できる。
一方でキューやprocess-loop、レーンの現物を照会・操作するには、参照資料に加えて対象へのアクセス権とCLIが必要になる。
それらの機能が利用できない環境では、机上の説明と実際の設定・状態変更とを区別して扱う。
運用操作は該当機能を利用できるClaude CodeまたはCodexで実行し、説明文には[文章の品質基準](references/quality-standards.md)を適用する。

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

## 読込表

| 時点または条件 | 全文読む資料 |
| --- | --- |
| 工程の責務、受渡しまたは出口を対策や説明の根拠にする前 | `${CLAUDE_PLUGIN_ROOT}/share/workflow-phases.md` |
| process-loop・run-skillの統括の挙動（起動、turn終了、再開、外からの制御）を前提に、案・回答・確認の選択肢を出す前 | `references/orchestrator-runtime.md` |
| 登録した複数リポジトリの担当起動・制御・再開を扱う前 | `../process-wi/references/all-repos-orchestration.md` |
| レーンの準備・統合・回復・回収を扱う前 | `../process-wi/references/run-lanes-launch.md`、`../process-wi/references/run-lanes-integration.md`、`../process-wi/references/lane-resources.md` |
| 開始後の新着WIの確認と今回追加の判断を扱う前 | `../process-wi/references/immediate-fix.md` |

## 利用形態と起動主体

| 利用形態 | 起動主体と公開操作 | 処理の責務 |
| --- | --- | --- |
| 対話型 | ユーザーがエージェントへ直接依頼する | エージェントが依頼を処理する。計画の作成要否は`agent-toolkit:write-plan`「計画ファイルの作成要否」、確認の要否は`agent-toolkit:user-confirmation-and-report`「確認要否の判定」に従って決める |
| 自律型 | ユーザーが`atk wi process-loop`を起動する | process-loopが開始前の更新・待機と、登録リポジトリ全体を処理する統括セッションの起動を管理する。統括は`agent-toolkit:process-wi all-repos`を実行する |
| まとめ処理型 | ユーザーが`agent-toolkit:process-wi single-lane`を手動起動する | 蓄積されたWIを1回の実行でまとめて処理する |
| 定期実行型 | ユーザーがcronなどに`atk run-skill`を登録する | WIキューを経由せずにスキル1件を自律モードで実行し、結果は報告用UWIで、判断を要する対応は事前承認型UWIで届ける |

process-loopの起動・待機・制御を扱う際は、事前に`atk wi process-loop --help`で利用条件を確認する。
公開されている主な操作には、中断を要求する`atk wi process-loop abort`、次の1セッションへの指示を与える`instruct`、状態を確認する`status`がある。
ホームディレクトリなどから`atk wi process-loop`を実行し、`repositories`で`process_loop`が有効な対象一覧を`atk wi list --configured-repos`で確認する。
リポジトリごとの編集可否・ローカルパス・process-loopの対象・検証OSは`atk config repos`で登録・変更・削除・一覧表示する。
未登録は編集不可であり、`allow`への登録は編集の認可を確認してから行う。
`awi`は直接編集せずAWIを投入し、`deny`は必要な変更を報告する。
常駐はprocess-wiの実行ごとに統括担当を1つ起動し、リポジトリごとの準備・所有・進捗を管理する。
統括は対象の規範・スキルを明示的に読み、今回の対象が1リポジトリならレーン数上限によらず自ら処理する。
対象が複数の場合も、今回進められる負荷が最大のリポジトリを統括が直接担当する。
全対象の上限が1以下なら統括が全件をまとめ、上限2以上の対象がある場合は、直接担当を除く軽い対象群を1担当へまとめ、重い残対象は個別に並行委譲する。
具体的な選定と通常の継続は`../process-wi/references/all-repos-starting.md`「開始と振り分け」に従う。
直接処理でも上限が2以上なら通常のレーン処理へ、1以下ならsingle-laneへ接続し、起動したwriterの終了観測と常駐への対象別結果の保存を保つ。
計画・検証・レビュー・公開は対象worktreeごとに分離し、process-wiの実行全体の完了報告は統括が行う。
常時処理するリポジトリだけ`process_loop`を有効にし、投入だけを行うリポジトリも`edit=awi`として登録できる。
非常駐の全体処理は`agent-toolkit:process-wi all-repos`を手動起動する。
同スキルは`atk wi process-loop --once`で、常駐と共通の対象取得・所有・worktree準備・結果検収・回収を1集合に対して行う。
対象が0件なら待機せず終了し、未完了・所有待ち・準備失敗が残れば非0で終了する。
表示された記録を確認し、`atk wi process-loop --once --auto-resume`で再開する。
手動起動元はCLIの終了を観測して結果を返し、完了報告とUWIの保存は統括が1回行う。
対象リポジトリの単発処理は`agent-toolkit:process-wi`または`agent-toolkit:process-wi single-lane`を使う。
登録や再開の条件は`../process-wi/references/all-repos-orchestration.md`に従う。

process-wiのメインセッションは対象の選定・配分とユーザー確認を担当し、レーン担当が計画、実装、実行レビューの調整、統合を順次進める。
初回の実行レビューでは詳細に点検し、レビュー限定基準への違反があれば指摘として通知する。
再レビューでは、前回の指摘事項、修正差分、および直接影響範囲を確認する。
レーン数によらない継続契約は`${CLAUDE_PLUGIN_ROOT}/share/single-lane-execution.md`に準拠する。
複数レーンを運用する場合はメインセッションが依存条件を満たしたレーンへ統合を許可し、統合先HEADの更新は`atk lane integrate`が1レーンずつに限る。
準備は`atk lane create`、統合は`atk lane integrate`、回収は`atk lane delete`の順で進め、各操作の実行前に`../process-wi/references/run-lanes.md`の該当節を確認する。
保持された資源の回復は、同書ならびに`atk lane delete --help`、`atk lane recover --help`に記載された条件や診断に従って再開する。

`run-skill`に由来するUWIは、`source`が`run-skill`、`scope`がスキル名となる。
`run_skill_report: true`を持つ報告用だけをprocess-wiの選定対象とprocess-loopの起動件数の計算から除外する。
報告用を除く回答済みの承認用UWIは、由来によらず既存の着手条件に従って対象に含める。

## 登録、回答、振り返り

目標から必要作業を構成する依頼は、通常対話と、単一リポジトリのprocess-wiへの追加指示で受け付ける。
通常対話は既存の作業契約と計画へ、process-wiは`../process-wi/references/lanes-goals.md`「明示された目標からの実行」へ接続する。
メインが全体の作業構成・結果の採否・方針転換・完了を所有し、局所担当が設計・実装、独立した担当が実行レビューを担う。
通常のキュー処理は初回の対象と配分を維持し、開始時集合との差分で新着を確認する。
新着の確認と今回追加の判断は`../process-wi/references/immediate-fix.md`「開始後に追加されたWIの確認」に従い、既存の作業記録へ対処時機と根拠を残す。

要求事項の登録は、ユーザーが`atk wi add`や`agent-toolkit:add-awi-by-user`、`atk serve`のWI管理画面から行うほか、処理中のエージェントや`agent-toolkit:review-session`からも投入できる。
本文の記述形式、由来、状態、依存関係の扱いは`agent-toolkit:wi-standards`に従う。

ユーザーの対応を必要とする未完了事項については、実行モードを問わず共通の確認基準に基づいて個別のUWIを登録するか判定し、報告時には保存先のパスを示す。
自明な情報の取得や再実行、実機確認の案内は通常の報告に添えて伝える。
手順で定められた完了報告用のUWIは適切に保存し、ユーザーの確認を受ける。
会話中のやり取りで回答を得られた事項はUWIとして重ねて登録せず、回答を得られないまま終了した確認事項はUWIへ引き継ぐ。
保存条件、自律モードにおける確認手段、およびUWIへの切り替え手順は`agent-toolkit:user-confirmation-and-report`「手段の選択」に従う。
ユーザーは`atk wi answer`または`atk serve`から回答を入力する。
回答済みのUWIについては、`run_skill_report: true`を持つ報告用は同じ対象リポジトリ・同じスキルの次回`atk run-skill`実行時に取り込む。
報告用を除く承認用は、process-wiの次回実行時にもメインセッションが処理へ取り込む。
`source`が`run-skill`の場合は`scope`のスキルを解決して承認後の手順を用い、解決できなければ続行できない理由を返す。
次回の定期実行は、process-wiが処理中のUWIを重複して取り込まない。
処理中のprocess-wiセッションに対してユーザーから回答が伝えられた場合は、同一セッションの処理対象に追加する（`../process-wi/references/selection-procedure-addition.md`「処理対象WIの追加」）。

作業完了後は`agent-toolkit:report-completion`で作業結果を報告して終了する。
Claude CodeとCodexの最上位セッションは振り返り待ちとして記録され、非活動になった対象を`agent-toolkit:review-session batch`が非同期でまとめて分析する。
batchが投入したAWIは、ユーザーによる内容確認を待たずに採否検討と処理の対象となる。
エージェント由来の要求の採否は`../wi-standards/references/wi-standards-origin.md`「由来と承認」に従って判断する。
手動の`agent-toolkit:review-session`は現在のセッション全体を対象とし、引数は追加の着眼点として扱う。
自動起動は`session_review_auto_start`で切り替え、無効時も待ち記録を保持して手動のbatchで処理できる。

## 運用変更を検討する観点

自動化の新設・運用変更では、起動契機、読み書きする状態とその所有者、実行環境、起動と制御、結果の観測を一続きに検討する。
成功・失敗・ユーザー判断待ちでどう終了し、何を契機にどの処理を再開するかを、判断結果を保持する既存の仕組みへ対応付ける。
同じ対象への重複起動と同じ事象からの重複起票、自動チェック・独立文脈レビュー・ユーザー判断と認可の分担も確認する。
個別の定義は既存の工程・確認・WI・委譲の契約を用いる。

別のprocess-loopが並行して稼働する前提で共有資源と状態遷移の競合・取りこぼしを確認する。
process-loop、子セッションのメイン、レーン担当、まとめ処理型のどこへ影響するかを整理し、各利用形態からの到達性とユーザーの操作負荷を比較する。
これらは自動化を設計・改訂するときの観点とし、通常のWI処理へ記入表や確認・審査工程を追加しない。

## 工程で参照する運用資料

| 時点または条件 | 全文読む資料 |
| --- | --- |
| 人間向け文書の役割と残す内容を選ぶ時 | `agent-toolkit:workflow-overview/references/document-types.md` |
| 既存の対象を名前で指す時と新しい名前を付ける時 | `agent-toolkit:workflow-overview/references/defined-names.md` |

## 本文参照の取得条件

| 時点または条件 | 全文読む資料 |
| --- | --- |
| ユーザー確認とユーザーへの報告の基準を適用する前 | `../user-confirmation-and-report/SKILL.md` |

# WI処理の運用概要

複数のセッションとユーザーの操作にまたがるWI処理の全体像である。
WI処理の工程や運用に関わる問題の原因分析と対策、運用工程や自動化の変更計画、ユーザーへの運用説明の前に読む。
各工程の入力と出口は`agent-toolkit:atk-mode/references/workflow-phases.md`、各工程の手順は`agent-toolkit:atk-mode`の作業`process-wi`と`agent-toolkit:atk-mode`の手順書が定める。
キュー、process-loop、レーンの現物を照会・操作できない環境では、机上の説明と実際の設定・状態の変更を分けて扱う。

## 利用形態と起動主体

| 利用形態 | 起動主体と公開操作 | 処理の責務 |
| --- | --- | --- |
| 対話型 | ユーザーがエージェントへ直接依頼する | エージェントが依頼を処理する。計画の要否は`agent-toolkit:atk-mode/playbooks/write-plan.md`「計画ファイルの作成要否」、確認の要否は`agent-toolkit:atk-mode/playbooks/confirmation.md`「確認要否の判定」で決める |
| 即時実装型 | ユーザーが`/atk-mode implement`を起動する | 対話セッションで隔離worktreeを作って実装・検証・実行レビューを完了し、一時リモートブランチをpushしてマージ用AWIを登録する。統合と公開は`process-wi`がレーンを作らず直接処理する |
| 自律型 | ユーザーが`atk wi process-loop`を起動する | process-loopが開始前の更新・待機と統括会話の起動を管理する。統括が`agent-toolkit:atk-mode`の作業`process-wi all-repos`で登録全体の対象を処理し、報告まで担当する |
| まとめ処理型 | ユーザーが`agent-toolkit:atk-mode`の作業`process-wi single-lane`を手動で起動する | 蓄積されたWIを1回の実行でまとめて処理する |
| 定期実行型 | ユーザーがcronなどに`atk run-skill`を登録する | WIキューを経由せずにスキル1件を自律モードで実行し、結果は報告用UWIで、判断を要する対応は事前承認型UWIで届ける |

process-loopの起動・待機・制御を扱う前に`atk wi process-loop --help`で条件を確かめる。
公開されている主な操作は、中断を要求する`abort`、次の1セッションへの指示を与える`instruct`、状態を確かめる`status`である。
常駐、単回実行、再開の予約、制御操作の違いは`agent-toolkit:atk-mode/references/orchestrator-runtime.md`にある。
process-loopはホームディレクトリなどから実行し、`process_loop`が有効な対象は`atk wi list --configured-repos`で確かめる。

リポジトリごとの編集可否・ローカルパス・process-loopの対象・検証OSは`atk config repos`で登録・変更・削除・一覧表示する。
未登録は編集不可で、`allow`への登録は編集の認可を確かめてから行う。
`awi`は直接編集せずAWIを投入し、`deny`は必要な変更を報告する。

保守ブランチと次期開発ブランチのように継続して作業が入るブランチが複数あるリポジトリは、そのブランチを系統として登録する。
登録のコマンドは`atk config repos set <リポジトリ> --branches <名前,名前> --default-branch <名前> --merge-from <取り込む側>=<取り込み元>`である。
系統を登録したリポジトリでは、WIが宛先の系統を`target_branch`に持ち、process-loopはリポジトリと系統の組を1つの処理対象として系統ごとの作業ツリーで処理する。
系統を省いたWIは`--default-branch`の系統へ入る。
両方の系統に要る修正は取り込み元の系統へ入れ、取り込む側はリリースの前に取り込み元をマージする。
WIでの指定は`agent-toolkit:atk-mode/playbooks/wi-saving.md`「投入と取得」に従う。
処理は`agent-toolkit:atk-mode/playbooks/process-wi.md`「系統を登録したリポジトリ」に従う。
リリース前の確認は`agent-toolkit:atk-mode/playbooks/publish.md`「プロジェクト固有の公開後の操作とリリース」に従う。

## 要求から公開までの担当

1. 要求の具体化: メインが元の要求・回答・既存の認可を確かめ、対象を選定して配分する。
   ユーザー固有の判断が要る箇所は、その回答に依存する処理の前に確認する。
2. 計画と実装: レーン担当が割り当てられた要求から計画・実装・検証を進め、実行レビューの入力を確定する。
   レーン数によらない継続と受け渡しの条件は`agent-toolkit:atk-mode/playbooks/process-wi-execution.md`に従う。
3. 独立文脈レビュー: 実装を担当していないレビュー担当が成果を検収する。
   再レビューは前回の指摘、修正差分、直接影響範囲を確かめる。
4. 統合: レーン担当が検収済みの成果を統合する。
   複数レーンではメインが依存条件を満たしたレーンへ統合を許可し、`atk lane integrate`は統合先HEADを1レーンずつ更新する。
5. 公開と後始末: メインが`agent-toolkit:atk-mode/playbooks/process-wi-publishing.md`の公開・CI・保存状態を検収し、報告と終了へ進む。
   準備・統合・回収の公開操作は`atk lane create`、`atk lane integrate`、`atk lane delete`で、実行前に`agent-toolkit:atk-mode/playbooks/process-wi.md`の該当節を確かめる。
   保持資源の回復は同書と`atk lane delete --help`、`atk lane recover --help`の条件と診断に従う。

必要なユーザー確認は特定の工程の末まで延ばさず、回答の後は次節の担当と契機で再開する。

`run-skill`に由来するUWIの`source`と`scope`は[由来と承認](origin-and-approval.md)で定め、報告用UWIだけに付ける属性は[完了報告](../playbooks/report-completion.md)で定める。
`run_skill_report: true`を持つ報告用だけを、process-wiの選定対象とprocess-loopの起動件数の計算から外す。
報告用を除く回答済みの承認用UWIは、由来によらず既存の着手条件で対象に含める。

## 要求の登録と判断待ちからの再開

目標から必要な作業を構成する依頼は、通常の対話と、単一リポジトリのprocess-wiへの追加指示で受け付ける。
通常の対話は既存の作業契約と計画へ、process-wiは`agent-toolkit:atk-mode/playbooks/process-wi-selection.md`「明示された目標からの実行」へつなぐ。
メインが全体の作業構成・結果の採否・方針転換・完了を所有し、局所の担当が設計・実装、独立した担当が実行レビューを担う。
通常のキュー処理は初回の対象と配分を保ち、開始後の新着の確認と今回追加の判断は`agent-toolkit:atk-mode/playbooks/process-wi-immediate-fix.md`「開始後に追加されたWIの確認」に従う。

要求の登録は、ユーザーが`atk wi add`、`/atk-mode add-awi-by-user`、`/atk-mode implement`、`atk serve`のWI管理画面から行うほか、処理中のエージェントと振り返り（`/atk-mode review-session`）からも投入できる。
本文の形式は`agent-toolkit:atk-mode/references/awi-body.md`と`agent-toolkit:atk-mode/references/uwi-format.md`に従う。
由来は`agent-toolkit:atk-mode/references/origin-and-approval.md`、状態と依存関係は`agent-toolkit:atk-mode/references/states-and-dependencies.md`に従う。

ユーザーの対応が要る未完了事項は、実行モードを問わず共通の確認基準で個別のUWIを登録するかを判定し、報告では保存先のパスを示す。
会話中に回答を得た事項はUWIとして重ねて登録せず、回答を得ないまま終わった確認事項はUWIへ引き継ぐ。
保存の条件、自律モードでの確認の手段、UWIへの切り替えは`agent-toolkit:atk-mode/references/delivery.md`「手段の選択」に従う。
ユーザーは`atk wi answer`か`atk serve`から回答する。
回答済みのUWIのうち、`run_skill_report: true`の報告用は同じ対象リポジトリ・同じ起動単位の次回の`atk run-skill`で、報告用を除く承認用は次回のprocess-wiでメインが取り込む。
`source`が`run-skill`の場合は`scope`のスキルを解決して承認後の手順を使い、解決できなければ続行できない理由を返す。
処理中のprocess-wiセッションへユーザーから回答が伝えられた場合は、同じセッションの処理対象へ加える（`agent-toolkit:atk-mode/playbooks/process-wi-additions.md`「処理対象WIの追加」）。

## 完了後の振り返り

作業の完了後は、報告を所有するメインが`agent-toolkit:atk-mode/playbooks/report-completion.md`で結果を報告して終わる。
Claude CodeとCodexの最上位セッションは振り返り待ちとして記録され、非活動になった対象を`/atk-mode review-session batch`が非同期でまとめて分析する。
batchが投入したAWIは、ユーザーの内容確認を待たずに採否の検討と処理の対象になり、エージェント由来の要求の採否は`agent-toolkit:atk-mode/references/origin-and-approval.md`「由来と承認」で判断する。
全量を指定せずに手動で起動した`/atk-mode review-session`は、確定済みの範囲より後を対象とする。
全量指定と引数は[単発の振り返り](../playbooks/review-session.md)を参照する。
`review-session meta`は`agent-toolkit:atk-mode/playbooks/review-session.md`「週次のメタ分析」で、キューと既存報告から反復と対策の効果を比較する。
metaは初回か前回の開始から7日以上経過後に最上位セッションの開始・終了で起動し、`add-awi-by-user`の相談中は見送る。
自動起動は`session_review_auto_start`で切り替え、無効でも待ち記録を保持して手動のbatchで処理できる。

## 運用変更を検討する観点

process-loopとprocess-wiは、品質を保って単位時間あたりに処理するWI数で評価する。
巡回・セッションの区切りは規範の再読み込みと`update-dotfiles`のために使い、区切り自体を完了の目的にせず、処理できるWIの開始を次の区切りまで待たせない。

自動化の新設と運用の変更では、起動の契機、読み書きする状態とその所有者、実行環境、起動と制御、結果の観測を一続きに検討する。
成功・失敗・ユーザー判断待ちでどう終わり、何を契機にどの処理を再開するかを、判断結果を保持する既存の仕組みへ対応付ける。
同じ対象への重複起動と同じ事象からの重複起票、自動チェック・独立文脈レビュー・ユーザー判断と認可の分担を確かめる。
別のprocess-loopが並行して稼働する前提で、共有資源と状態遷移の競合・取りこぼしを確かめ、process-loop、子セッションのメイン、レーン担当、まとめ処理型のどこへ影響するかと、各利用形態からの到達性とユーザーの操作負荷を比べる。
これらは自動化を設計・改訂する時の観点で、通常のWI処理へ記入表や確認・審査の工程を足さない。

## 作業用の文書と形式の定義元

文書の種類と読者の選び方は`agent-basics:writing-standards/references/document-types.md`にある。
agent-toolkitが使う作業用の文書は、次の定義に従う。

| 文書 | 形式の定義 |
| --- | --- |
| AWI・UWI（ユーザーと実行担当が要求と確認事項を共有する） | `agent-toolkit:atk-mode/references/awi-body.md`、`agent-toolkit:atk-mode/references/uwi-format.md` |
| 計画ファイル | `agent-toolkit:atk-mode/references/plan-format.md` |
| 実行レビュー指摘管理表（実装担当とレビュー担当が是正の状況を共有する） | `agent-basics:review-standards`、`agent-toolkit:atk-mode/references/review-record-table.md` |
| コミットメッセージ | `agent-toolkit:atk-mode/references/commit-message.md` |
| PR・MRの説明、Release本文 | `agent-basics:writing-standards/references/writing-genres.md`「判断と行動を求める記述」。投稿の前は`agent-toolkit:atk-mode/playbooks/review-external-writes.md` |

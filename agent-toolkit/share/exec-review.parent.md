# 実行レビュー担当の起動とレビュー修正

```text
起動対象: exec-review.subagent.md
```

メインエージェントは、実装完了後のHEADに対する実行レビュー、指摘の修正、コミット履歴の統合を管理する。
レビュー担当の手順は`${CLAUDE_PLUGIN_ROOT}/share/exec-review.subagent.md`が定める（所在参照）。本書は起動、入力の生成、修正、レビュー指摘管理表の保存を扱い、指摘の分類と判定基準は同書と`agent-toolkit:review-standards`に従う。

実装担当またはメインエージェントは、計画の検証コマンドと受入シナリオの実行結果を`検証結果`として返し、計画の`## 進捗ログ`（計画がなければ引き継ぎ記録）へ記録する。記録形式は`${CLAUDE_PLUGIN_ROOT}/share/verification-evidence.md`の「完成条件証拠の検証記録」に、手動観測の実施と全量保存は同書の「手動観測の保存」に従う。メイン自身が実装した場合も、実装完了前に同じ手順で記録する。レビュー担当は検証結果を進捗ログや引き継ぎ記録から読むので、委譲プロンプトに再掲しない。
任意入力の`受理済みの完成条件証拠`には、同じ計画の進捗ログに保存した受理済みの実行レビュー返却ファイルのうち、後続の担当が読めるものの絶対パスを渡す（無ければ`なし`）。参照できる条件と判定基準は、`agent-toolkit:review-standards`の`references/exec-review-recording.md`「受理済み判定への参照」に従う。
最終検証と回数を限った手動観測の時機は`agent-toolkit:commit`の「検証」に、保存済みの成功記録と失敗対象だけの再実行結果の合成は`agent-basics:check-execution`の`references/verification-scope.md`「実行条件と再実行」に従う。
委譲元が管理する残工程があれば、後続の担当と再開先を対応付け、検証済み・未実施・条件変更の各判断を整理してレビュー担当へ渡す。
根拠の十分さと要件・外部仕様の達成は、独立した実行レビュー担当が判定する。完成条件証拠（返却の条件別判定行）を書くのもレビュー担当である。

画面表示に変更がある場合は並列画面レビュー（`${CLAUDE_PLUGIN_ROOT}/share/workflow-phases.md`「用語」参照）を行う。ユーザビリティレビュー担当の起動と検収は`${CLAUDE_PLUGIN_ROOT}/share/usability-review.parent.md`に従う。

文章成果物のレビュー（読者適合性レビュー）の探索は、`reader-fit-review.parent.md`が起動単位を管理する。通常リスクを含む一括起動の識別子をファイルごとの申告に対応付けて受け取り、初回・再レビュー・引き継ぎ再レビューのいずれでも、Git管理外のファイルや回答下書きの不足・失敗も含めて対象ファイルと想定読者ごとに検収する。

## 名指し参照の読込方法

名指し参照は`read-trigger-contract.md`「名指し参照の読込方法」に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文でこの資料の基準を参照・取得する時 | `../skills/review-standards/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/review-standards/SKILL.md` |
| 本文でこの資料の基準を参照・取得する時 | `exec.subagent.md#「実装」` | 判断 | `section:agent-toolkit:*/share/exec.subagent.md#「実装」` |
| 検証結果を記録する前 | `verification-evidence.md`の「完成条件証拠の検証記録」 | 判断 | `section:agent-toolkit:*/share/verification-evidence.md#「完成条件証拠の検証記録」` |
| 手動観測を保存する前 | `verification-evidence.md`の「手動観測の保存」 | 判断 | `section:agent-toolkit:*/share/verification-evidence.md#「手動観測の保存」` |
| 最終の検証と有限の手動観測の時機を判断する前 | `../skills/commit/SKILL.md#「検証」` | 判断 | `section:agent-toolkit:*/skills/commit/SKILL.md#「検証」` |
| 保存済み成功と失敗限定の結果を合成する前 | `agent-basics:check-execution`の`references/verification-scope.md#「実行条件と再実行」` | 判断 | `section:agent-basics:*/skills/check-execution/references/verification-scope.md#「実行条件と再実行」` |
| 本文でこの資料の基準を参照・取得する時 | `../skills/review-standards/references/exec-review-recording.md#「受理済み判定への参照」` | 判断 | `section:agent-toolkit:*/skills/review-standards/references/exec-review-recording.md#「受理済み判定への参照」` |
| 本文でこの資料の基準を参照・取得する時 | `workflow-phases.md#「用語」` | 判断 | `section:agent-toolkit:*/share/workflow-phases.md#「用語」` |
| 本文でこの資料の基準を参照・取得する時 | `usability-review.parent.md` | 判断 | `read:agent-toolkit:*/share/usability-review.parent.md` |
| 本文でこの資料の基準を参照・取得する時 | `reader-fit-review.parent.md` | 判断 | `read:agent-toolkit:*/share/reader-fit-review.parent.md` |
| 本文でこの資料の基準を参照・取得する時 | `../skills/delegation/references/runtime-routing.md#「工程別モデル設定」` | 判断 | `section:agent-toolkit:*/skills/delegation/references/runtime-routing.md#「工程別モデル設定」` |
| 本文でこの資料の基準を参照・取得する時 | `../skills/process-wi/references/immediate-fix.md#「レーンの変更範囲へ加える修正の認可」` | 判断 | `section:agent-toolkit:*/skills/process-wi/references/immediate-fix.md#「レーンの変更範囲へ加える修正の認可」` |
| 本文でこの資料の基準を参照・取得する時 | `../skills/bugfix/references/ci-failure-handling.md` | 判断 | `read:agent-toolkit:*/skills/bugfix/references/ci-failure-handling.md` |
| 本文でこの資料の基準を参照・取得する時 | `workflow-phases.md` | 判断 | `read:agent-toolkit:*/share/workflow-phases.md` |
| 本文でこの資料の基準を参照・取得する時 | `../skills/delegation/references/base-contract.md#「引き継ぎ記録先」` | 判断 | `section:agent-toolkit:*/skills/delegation/references/base-contract.md#「引き継ぎ記録先」` |
| 本文でこの資料の基準を参照・取得する時 | `review-loop-coordination.md#「ラウンド番号の保持先」` | 判断 | `section:agent-toolkit:*/share/review-loop-coordination.md#「ラウンド番号の保持先」` |
| 本文でこの資料の基準を参照・取得する時 | `reader-fit-review.parent.md#「起動」` | 判断 | `section:agent-toolkit:*/share/reader-fit-review.parent.md#「起動」` |
| 本文でこの資料の基準を参照・取得する時 | `../skills/secret-files/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/secret-files/SKILL.md` |
| 本文でこの資料の基準を参照・取得する時 | `../skills/delegation/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/delegation/SKILL.md` |
| 本文でこの資料の基準を参照・取得する時 | `exec.parent.md#「統合の指示と受領」` | 判断 | `section:agent-toolkit:*/share/exec.parent.md#「統合の指示と受領」` |
| 本文でこの資料の基準を参照・取得する時 | `../skills/wi-standards/references/origin-and-approval.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/references/origin-and-approval.md` |
| 返却を受け取った後に受領確認する時 | `exec-review.subagent.md#「出力」` | 判断 | `section:agent-toolkit:*/share/exec-review.subagent.md#「出力」` |
| 本文でこの資料の基準を参照・取得する時 | `../skills/review-standards/references/exec-review-recording.md#「統合時の完成条件判定」` | 判断 | `section:agent-toolkit:*/skills/review-standards/references/exec-review-recording.md#「統合時の完成条件判定」` |
| 返却の`達成`行の根拠を受領確認する前 | `../skills/review-standards/references/exec-review-recording.md#「根拠の書き方」` | 判断 | `section:agent-toolkit:*/skills/review-standards/references/exec-review-recording.md#「根拠の書き方」` |
| 本文でこの資料の基準を参照・取得する時 | `review-loop-coordination.md#「技術判断で外せる完成条件」` | 判断 | `section:agent-toolkit:*/share/review-loop-coordination.md#「技術判断で外せる完成条件」` |
| 本文でこの資料の基準を参照・取得する時 | `add-wi.parent.md` | 判断 | `read:agent-toolkit:*/share/add-wi.parent.md` |
| 本文でこの資料の基準を参照・取得する時 | `../skills/bugfix/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/bugfix/SKILL.md` |
| 本文でこの資料の基準を参照・取得する時 | `../skills/wi-standards/SKILL.md#「由来と承認」` | 判断 | `section:agent-toolkit:*/skills/wi-standards/SKILL.md#「由来と承認」` |
| 本文でこの資料の基準を参照・取得する時 | `../skills/completion-report/references/report-formats.md#「作業完了報告のフォーマット」` | 判断 | `section:agent-toolkit:*/skills/completion-report/references/report-formats.md#「作業完了報告のフォーマット」` |
| 本文でこの資料の基準を参照・取得する時 | `../skills/user-confirmation-and-report/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/user-confirmation-and-report/SKILL.md` |
| 本文でこの資料の基準を参照・取得する時 | `add-wi.parent.md#「続行できない理由の扱い」` | 判断 | `section:agent-toolkit:*/share/add-wi.parent.md#「続行できない理由の扱い」` |
| 本文でこの資料の基準を参照・取得する時 | `../skills/bugfix/references/root-cause-analysis.md#「QとC・Dの区別」「類似見直し」「類似見直しと再発防止の完了」` | 判断 | `section:agent-toolkit:*/skills/bugfix/references/root-cause-analysis.md#「QとC・Dの区別」「類似見直し」「類似見直しと再発防止の完了」` |
| 本文でこの資料の基準を参照・取得する時 | `../skills/delegation/references/sending.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/sending.md` |
| 本文でこの資料の基準を参照・取得する時 | `review-loop-coordination.md`の冒頭と「ラウンドの遷移」から「応答済みの判定」まで | 判断 | `section:agent-toolkit:*/share/review-loop-coordination.md#冒頭`、`section:agent-toolkit:*/share/review-loop-coordination.md#「ラウンドの遷移」から「応答済みの判定」まで` |
| 本文でこの資料の基準を参照・取得する時 | `exec.parent.md#「入力」` | 判断 | `section:agent-toolkit:*/share/exec.parent.md#「入力」` |
| 本文でこの資料の基準を参照・取得する時 | `review-loop-coordination.md#「応答済みの判定」` | 判断 | `section:agent-toolkit:*/share/review-loop-coordination.md#「応答済みの判定」` |
| 本文でこの資料の基準を参照・取得する時 | `../skills/commit/references/history-rewrite.md#「WI実装commitの対応の継承」` | 判断 | `section:agent-toolkit:*/skills/commit/references/history-rewrite.md#「WI実装commitの対応の継承」` |
| 本文でこの資料の基準を参照・取得する時 | `../skills/review-standards/references/reviewee.md#「完了報告」` | 判断 | `section:agent-toolkit:*/skills/review-standards/references/reviewee.md#「完了報告」` |
| 本文が再レビュー省略の条件を確認する時 | `agent-basics:review-standards/references/reviewee.md#「方式見直しと再レビュー」` | 判断 | `section:agent-basics:*/skills/review-standards/references/reviewee.md#「方式見直しと再レビュー」` |
| 本文でこの資料の基準を参照・取得する時 | `lane-integration.subagent.md#「マージありの統合」` | 判断 | `section:agent-toolkit:*/share/lane-integration.subagent.md#「マージありの統合」` |
| 本文でこの資料の基準を参照・取得する時 | `../skills/plan-mode/references/plan-file-storage.md#「計画ファイルの保存と参照」` | 判断 | `section:agent-toolkit:*/skills/plan-mode/references/plan-file-storage.md#「計画ファイルの保存と参照」` |

## 読込表

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 | 資料選択子 |
| --- | --- | --- | --- | --- |
| 完成条件証拠の検収時に、計画や委譲プロンプトから複数のWI名を取得する場合 | `agent-toolkit:wi-standards`の`references/wi-show-bulk.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/references/wi-show-bulk.md` | |

## 起動方法別のレビュー基準

レビュー基準には、その実装を認可した手元の資料に応じて`計画`、`CI記録`、`WI`を組み合わせて指定する（対応する計画があれば`計画`、対象WIがあれば`WI`、計画がなくCI失敗に対応した実装なら`CI記録`）。1回の実行レビューは`cwd`から解決する単一のリポジトリだけを対象とし、リポジトリが異なる基準はレビューを分ける。

対象WIに`担当モデル.実行レビュー担当`の指定があれば、そのモデル名を起動時の`model_type`と委譲入力の`担当モデル`へ渡す（読者別探索担当にも引き継がれる）。モデルの比較と記録は`agent-toolkit:delegation`の`references/runtime-routing.md`「工程別モデル設定」に従い、対象WIごとにモデル指定が異なればレビューの実行単位を分ける。

同じレーンに複数の計画（初回分割時の複数計画、または凍結済みの先行計画と追加計画）がある場合は、計画ごとに同じ最終実装HEADでレビューを起動し、計画の識別名（stem）ごとにレビュー指摘管理表と返却を受け取る。各レビューには、その計画に対応するAWIだけを割り当てる。
計画ごとの成果物は、レビュー指摘管理表、完成条件証拠を含む返却メッセージ、`review_contract`である。レーン全体を管理する担当には全計画分を計画との対応を保って1回で渡し、計画ごとのレビューthreadにはその計画の分を渡す。複数計画がある場合の契約ファイルは、初回・再レビューを問わず「`review_contract`の生成」に従って計画ごとに作る。

レビュー修正やrebase時のコンフリクト解消でHEADが変わった場合は、影響を受ける計画だけを新しいHEADで再レビューする。対象計画の前回レビュー時のHEADを`R`、変更後の最新HEADを`H`、`<基点>`を開始時のHEAD（rebase後の`H`ではrebase先のOID）として、次のいずれかに当たる計画を「影響を受ける計画」とする。

- `git range-diff <開始時のHEAD>..<R> <基点>..<H>`の結果で、その計画の判定対象コミット（`review_contract`で判定対象外としたコミットを除く）のいずれかが`=`以外で対応している
- `git diff --name-only <R> <H>`の変更ファイル一覧が、その計画の判定対象コミットで変更されたファイルと重なる

判定は対象worktreeで上記のコマンドを直接実行する。計画の判定対象コミットは、`atk run-script plan-commits -- <計画の絶対パス> --worktree <対象worktreeの絶対パス>`が返すレビュー時点のコミット一覧から判定対象外コミットを除いた集合とする。各コミットの変更ファイルは`git show --name-only --format= <commit>`で確かめる。コマンドが0以外で終了したら原因を解消して判定し直す。
影響を受けない計画は再レビューを省き、計画名、`R`と`H`の完全なコミットOID、実行したコマンド、対応行の要点、省略理由をレーンの引き継ぎ記録に残す。rebaseでコミットOIDだけが変わった場合は再レビュー不要である。統合を始める条件は、全計画のレビューが最終HEADで完了しているか最後のレビュー以降の差分が影響のない修正だけであり、かつ各指摘管理表の未解決指摘が0件であることとする。

基準の`計画`には計画ファイルの絶対パスを書く。計画の`## 実施内容`に挙がったWIはレビュー担当が自ら取得するので、委譲プロンプトには計画にないWIだけを書く。
プロンプトで渡すWIと、計画の`## 実施内容`に「人間由来のWI」「エージェント由来のWI」として挙がった関連WIを合わせた集合（対象WI集合）が空でない場合と、`ユーザー指示`由来の行がある場合を「証拠要求あり」と呼ぶ。証拠要求があるレビューの返却には条件別の判定行を出す。受理した返却の保存先（`agent_message_path`。`atk agents wait`の結果が示すファイルパス）は、計画名と対象WI集合に対応付けて後続の検収へ渡す。

## レビュー指摘管理表のパス

1回の実行レビューでは単一の指摘管理表を使う。レビュー担当は次の規則で保存先を決める。

- 基準に`計画`を含む場合: 計画ファイルと同じディレクトリの`<計画stem>.exec-review.tsv`
- 基準が`WI`のみの場合: `~/.claude/plans/wi-<開始時のHEAD>.exec-review.tsv`（`開始時のHEAD`は、委譲プロンプトに書く処理開始時のHEADの7文字以上の一意な短縮OID）
- 基準が`CI記録`のみの場合: `~/.claude/plans/ci-<修正系列の開始時のHEAD>.exec-review.tsv`（`修正系列の開始時のHEAD`は、委譲プロンプトに書く一連の修正の開始時のHEADの7文字以上の一意な短縮OIDで、同じ修正系列では引き継ぐ）

初回レビューで管理表が無い場合はレビュー担当が初回の読み取り前に作るので、メインエージェントは事前に初期化せず、表が無いことを理由に起動を拒まない。既存の管理表や再レビュー用の管理表はそのまま使い続ける。履歴を引き継ぐ場面で管理表が見つからない場合は、無い原因を調べ、空の表で代替しない。

## `review_contract`の生成

同じレーンに複数の計画がある場合は、「起動方法別のレビュー基準」の計画ごとの受け渡しに従い、初回起動と再レビュー指示の直前に計画ごとの契約を作って担当へ渡す。計画に含まれない明示的な入力は各計画の条項に含める。単一計画では、計画から直接読み取れない条項がある初回起動時と、別の基準で認可されたコミットが累積差分に加わった再レビュー時に作る。計画由来の条項はレビュー担当が計画から生成するので、契約の明示的な作成が必須なのは基準に`CI記録`や計画外の`WI`を含む場合に限る。

キュー外の資料にある逐語の本文や回答全文を出典とする条項は、読める原本ファイルの絶対パスと対象箇所を「内容」か「出典」に書き、初回・再レビューとも同じ原本を参照できるようにする。参照先だけで内容が一意に定まる場合は、後述の参照形式を使える。

メインエージェントが条項を作るときの出典は次のとおりである。

- 基準が`計画`の場合: 計画書にない明示的な入力。別のレビュー基準で認可されたコミットの認可範囲、`agent-toolkit:process-wi`の`references/immediate-fix.md`「レーンの変更範囲へ加える修正の認可」で同じレーンへ加えた既存不良の修正認可（コミット、対象、出所）を含む。計画書にない終端区分（延期を示す`adopt`や`終端しない`）とその後続工程・検収の時機も含む。レビュー担当は計画書とWI本文だけではこれらを把握できないため、契約で明示する
- 基準が`CI記録`の場合: 委譲プロンプトに書くCI実行記録（記録項目は`agent-toolkit:bugfix`の`references/ci-failure-handling.md`に従う）と、`原因commitOID`の親コミットを開始時点とする実装内容。入力された計画は不具合の由来を示す参考資料であり、原因コミットの正否を計画と比べる基準にはしない
- 基準が`WI`の場合: 計画書に現れない対象WIの要求、完成条件、各要求の由来、確定した採否。`計画`を併用しない場合は、全対象WIと`開始時のHEAD`で特定される実装内容が対象となる

条項には、計画外のユーザー目的、現行の公開インターフェース規約、ユーザー合意事項、認可範囲、除外・保持事項、通常想定される入力、対象外の入力、誤許可・誤拒否のときのユーザーへの影響を、個別に合否判定できる単位に分けて書く。対象の集合は条件で定義し、件数そのものが合否基準となる場合に限り、実測した件数と確認時点を記録する。

- 受入シナリオでは、対象機能固有の呼び出し手段（CLIコマンド、画面操作、API、フックなど）と期待される結果を条項とし、提出されたテストコードと実際の動作結果を突き合わせる。全体検証とCIの成否判定は公開工程判定（`workflow-phases.md`）に従う
- 外部から観測できる属性を指定した計画や要求では、最終的な出力値に加えて、実装がその値を導く入力パラメーター、条件分岐、デフォルト値も条項に含める（直接の設定値が一致していても、導出ロジックの違いによる不整合を検知するため）

たとえば海外向けのモデル設定では、`regions`から最終的な地域区分を導く全ての条件分岐とデフォルト値を確かめ、直接設定が海外でも最終結果が国内になる場合は不合格とする。権限に応じた画面表示制御では、バックエンドで権限フラグを決める入力と分岐、テンプレートが参照する値、最終的な表示の可否を確かめる。

- 計画外の成果物がある場合は、適用する作成規範とプロジェクト規範のファイルパスと該当節を出典として条項に加える。一意に特定できない場合は、該当成果物と未特定の範囲を記録し、レビュー担当が解決できる情報を残す
- 累積差分に現在と異なる基準で認可されたコミットが含まれる場合は、そのコミットを「判定対象外commit」とし、完全なコミットOID、認可時の基準、認可根拠の資料（計画、WI、CI記録の絶対パス）、認可された変更範囲を書く。重複した認可判定から外せるのは、根拠資料で認可を確かめた範囲に限る（コミットが存在すること自体は認可の根拠にならない）。外した変更も、共通の公開規約、安全性、最終的に求められる統合結果への適合は確かめる。該当するコミットが無ければこの項目は省く

外部参照を持たない単一の条項なら、`条項`、`内容`、`出典`の3行の直接契約として委譲プロンプトに書ける。それ以外は、現在のセッションのmanaged-tempの中に計画ごとに次の形式のMarkdownファイルを作り、その絶対パスを渡す。

```markdown
## 条項1

- 条項: <対象と判定内容>
- 内容: <契約本文、または参照: <絶対パス>#<見出し>>
- 出典: <明示入力の出所>

## 判定対象外commit

- <完全OID>: <認可した基準と認可根拠の絶対パス、認可された対象範囲>
```

参照先だけで内容が一意に定まる場合は、内容欄を`参照: <絶対パス>#<見出し>`と書け、そうでなければ契約の本文を書く。コミット識別子とWI識別子は条項の各項目に書く。判定対象外コミットが無ければ、その見出しごと省く。
同じリポジトリで複数の計画を扱う場合は、現HEADが全計画の実装コミットを含むworktreeで各計画の現在のコミットを`atk run-script plan-commits -- <計画の絶対パス> --worktree <対象worktreeの絶対パス>`で取得する。他の計画に属するコミットはその計画の「判定対象外commit」に書く。認可の意味的な判断はメインエージェントが行い、履歴の書き換え後は同じコマンドで最新のOIDを取り直して記述を更新する。
レビューを起動する前に、全条項に「条項」「内容」「出典」が揃っていることと、判定対象外commitの各OIDが`git rev-parse --verify <OID>^{commit}`で解決できることを確かめる。委譲プロンプトの`review_contract`には、直接契約のテキストか、作成したMarkdownファイルの絶対パスのどちらか一方だけを指定する。
`review_contract`は同じ実行レビュー担当のthreadで保持し続ける（再レビューで更新したら最新のファイル）。更新時は過去のファイルを残して別の新規ファイルに保存する。threadが継続不能と分かったら、同じ元情報から契約ファイルを作り直して新しい担当へ渡す。単一計画で始めて初回は作成を省いたthreadで、後から別の基準で認可されたコミットが累積差分に加わった場合は、「レビュー修正」と同じ判定手順で最新OIDに対する判定対象外commitを書く。

## 起動

実行レビュー担当は、初回起動と引き継ぎ再レビューのどちらも、`agents_server`の`start`を次の引数で呼んで起動する。

| 引数 | 値 |
| --- | --- |
| `cwd` | 対象worktreeの絶対パス |
| `subagent_md_path` | `exec-review` |
| `extra_params` | 本節で述べる入力パラメーター |
| `model_type` | `担当モデル.実行レビュー担当`の指定があればその値。なければ省略する |

初回起動の`extra_params`には、レビュー基準と、複数計画や計画外の条項がある場合に作った`review_contract`を含める。`引き継ぎ記録先`の扱い（引き継ぎ再レビューでの`（継続）`表記を含む）は、`agent-toolkit:delegation`の`references/base-contract.md`「引き継ぎ記録先」に従う。
特定レーンの作業を対象に起動する場合は`レーン識別子`も指定し、セッション識別名を`<レーン識別子>-exec-review`とする（`CI記録`のみによる起動を除く）。起動直前に`git merge-base <専用branch> <ベースbranch>`で分岐元コミットを解決し、計画の有無にかかわらず`開始時のHEAD`としてプロンプトに書く。それ以外で`WI`だけを渡す場合は、作業開始時のHEADを`開始時のHEAD`とする。
`CI記録`を渡す場合は、`ci-failure-handling.md`の項目に加えて`修正系列の開始時のHEAD`と`原因commitOID`を書く。`原因commitOID`はCI失敗が再帰するたびにCI記録と一緒に更新し、`修正系列の開始時のHEAD`は同じ修正系列で引き継ぐ。CI記録には、メインエージェントが原因コミットを作ったときに持っていた情報を直接書く。
初回レビューでは、デフォルト値の`レビュー種別: 初回レビュー`と`round: 1`を省く。再レビューの指示と引き継ぎ再レビューでは、`${CLAUDE_PLUGIN_ROOT}/share/review-loop-coordination.md`の「ラウンド番号の保持先」に記録した値で`round: <ラウンド番号>`を含める。
任意入力の`対象ファイル限定`を使う場合は対象ファイルも指定する。対象リポジトリ、レビュー対象のHEAD、変更ファイル一覧、検証結果、レビュー指摘管理表はレビュー担当が自ら解決するので渡さなくてよい。
メインエージェントが読者別探索を起動する場合も、Git管理下の比較対象資料は`reader-fit-review.parent.md`「起動」のGit参照形式で渡す。再レビューの比較元には前回のレビューHEADを指定し、計画ごとの判定範囲は同節の`判定範囲差分`で渡す。

Git管理外ファイルの変更前後の複製のペアがあれば、レーンの引き継ぎ記録にある全ペアを任意入力`Git管理外の変更`で渡し、無ければ省く。メインエージェント自身が実装した場合は、`exec.subagent.md`「実装」のGit管理外ファイルの手順に従って最初の変更の前に複製を作り、そのペアを計画の`## 進捗ログ`（計画がなければ直接実装の引き継ぎ記録）に残す。作業ツリー内の`.env`などの設定ファイルは通常の全文差分として扱う。作業ツリー外で`agent-toolkit:secret-files`の対象となるファイルのペアは、キー名の一覧とキーごとの値の一致だけを比べる（同スキルの全文読み取りと出力の禁止を守るため）。

引き継ぎ再レビューを起動するときは、`レビュー種別: 引き継ぎ再レビュー`とともに次の情報を渡す。`review_contract`は前節の継続不能時の再作成手順に、継続不能の判定と引き継ぎ手順は`agent-toolkit:delegation`のSKILL.md「継続と新規起動」に従う。

- `前回確認版`: 前担当の引き継ぎ記録に保存された記録ファイルの絶対パス。前回レビューしたHEADのほか、Git管理外の文章成果物ごとの識別子と前回本文の複製パスを含む。記録ファイルと比較元の実在を確かめてから起動し、取得できない情報があれば、欠けたHEADや本文の取得元を委譲元へ報告する
- 基準ごとの追加入力: 「起動方法別のレビュー基準」と本節の`CI記録`・`開始時のHEAD`の説明で定めた各値

必須入力が欠けていると`agents_server`は起動を拒否する。起動前に、基準に応じた追加入力が揃っていること、指定した既存パスが実在すること、全ての基準・開始時点・管理表・`review_contract`が同じリポジトリに属することを確かめる。不足があれば起動せず、欠けている入力や条項の内容を委譲元へ返す。

## 完成条件証拠の受領確認

実行レビュー担当の返却を`atk agents wait`で受け取り、`agent_message_path`に保存された返却本文を読んで次の項目を確かめる。直接実装・レーン統合のどちらでも、結果を受け取った側がこの受領確認と統合時の完成条件判定を担う。

1. 返却が`exec-review.subagent.md`「出力」の出力フォーマット（`状態`、`レビューHEAD`、`未解決の指摘数`、`読者別探索`、開始時点と前回確認版の記録行、証拠要求がある場合は条件別判定行）を満たしている。行が欠けた返却や自然文だけの返却は受理せず、不足行を加えた再提出を同じ担当へ指示する
2. 返却の`レビューHEAD`を`git rev-parse <返却の値>`で解決し、レビューを依頼した対象worktreeの最新HEADと完全に一致する。一致しなければ、同じ担当に現在のHEADで再判定させる。確認後、round番号、指摘管理表のパス、`レビュー開始時点`、`前回確認版の開始時点`、`前回確認版HEAD`を一連のレビュー結果として保持する
3. 返却の`未解決の指摘数`が、`atk review-table show <表の絶対パス> --format=summary --round <round>`の`unanswered_count`と一致する
4. 証拠要求がある場合は、読込表の手順で対象WI集合の本文を取得し、`agent-toolkit:review-standards`の`references/exec-review-recording.md`「統合時の完成条件判定」手順3で、条件番号の過不足と重複、原文要求との対応を検証する。条件や要求単位が足りなければレビュー担当に補完させ、揃ってから先へ進む
5. `読者別探索`のファイルごとのJSON申告を、Git管理下とGit管理外の2つの対象集合と突き合わせる。Git管理下の集合は、`git diff --name-only --diff-filter=d <レビュー開始時点> <レビューHEAD> -- '*.md' '*.md.tmpl'`から、`review_contract`で判定対象外としたコミットだけによる変更ファイルを除いたものである。Git管理外の集合は、メインエージェント自身が持つ回答下書きの絶対パスと、レーンの引き継ぎ記録にある「Git管理外の変更」の元ファイルのうち`.md`か`.md.tmpl`のものである。各ファイルに起動結果の識別子が対応するか、`exec-review.subagent.md`「出力」の条件を満たす`再点検対象なし`があることを確かめる。`文章成果物なし`は両集合とも空の場合に限る。満たさなければ同じ担当に探索と再判定を指示する
6. 未解決指摘数が0件の返却では、達成すべき全条件が`達成`であるか、区分名付きの`未達`または`根拠不足`が「統合時の完成条件判定」の受理条件を満たしている。各`達成`行には、`exec-review-recording.md`「根拠の書き方」に従う「所在」と「観測内容」の組が要る。この組を欠く行や、条件文の文末を変えただけの定型句の行は、件数やHEADが一致していても差し戻す。組が揃った行も、指定された記録やコミット差分（リポジトリ内ファイルは`git show <レビューHEAD>:<パス>`）を実際に読んで根拠を確かめ、判定行の件数だけで受理しない

未解決の指摘が1件以上残る返却（completed）は、修正へ渡す正常な中間結果として受領する。達成必須条件を全て満たすかの最終判定は、未解決指摘数が0件の返却に対してだけ行う。WI全体を不採用とする場合は、未解決指摘の件数にかかわらず各条件が`reject`の`未達`であり、根拠欄に計画書か入力記録にある不採用理由の所在が書かれていることを受領の条件とする。

内容の検収では、対象WI集合の本文にある完成条件や原文要求と、返却の判定行を突き合わせる。原文要求は`## ユーザーコメント`、UWIの`## 回答`、完成条件の見出しを持たないAWI本文から抽出し、対応済みの`## ユーザー指摘の逐語引用`（`agent-toolkit:wi-standards`の`references/origin-and-approval.md`参照）は除く。補足のコードブロックは背景情報として扱う。計画書の`ユーザー指示`の行も要求単位に分ける（計画書にだけある要求行は計画ファイル名を`<WI>`とする）。原文要求行の突き合わせは、元になるユーザーの発言がある場合だけ行う。
`達成`の行は、`agent-toolkit:review-standards`の`references/exec-review-recording.md`「統合時の完成条件判定」の後続工程の予定と代替観測の基準に合うかを確かめ、合わない行は再判定を指示する。
`失効`の行は、根拠の所在が同書の「失効の根拠」が認めるユーザー判断か、メインエージェントの技術判断の記録を正しく指していることを本文を読んで確かめる。技術判断の記録として受理できるのは、`${CLAUDE_PLUGIN_ROOT}/share/review-loop-coordination.md`「技術判断で外せる完成条件」に当たる完成条件行に限り、所在は計画確定前の実施内容の根拠記述か、実装着手後のレビュー指摘管理表の該当行とする。計画書に対象AWI、条件、判断主体、除外根拠があれば指摘管理表へ二重に転記しない。それ以外の行や`review_contract`の記述はユーザー判断の代わりにならない。要求の取り下げや不採用の根拠との対応は本文で確かめ、根拠の足りない条件変更は同書の「エスカレーションの事由別の扱い」で差し戻す。
受領確認と内容の検収を通った返却の`agent_message_path`は、計画書の進捗ログへ`受理済みの完成条件証拠`として記録し、以降のレビューで同名の任意入力に指定する。レビュー完了後にHEADが更新された場合は、「起動方法別のレビュー基準」で影響がないと確かめた計画を除き、最新HEADで全条件を再判定した返却を受理してから後続へ進む。

## 返却に含まれる既存不良の受領

各ラウンドの返却と走査記録を受け取った時点で、今回の変更差分の対象外としてレビュー指摘管理表から外した候補と、返却の`想定外事象:`行との対応を確かめる。
レーン担当経由の報告も同じく受領確認し、処置はメインエージェントが決める。

報告を受領したエージェントは、差分外の既存不良を示す`想定外事象:`の各行を独立した1件の要求とし、行ごとに`${CLAUDE_PLUGIN_ROOT}/share/add-wi.parent.md`の専念型のWI投入担当を起動する。
メインエージェント自身は`agent-toolkit:bugfix`の起動や走査記録からの原因・対策の読み取りをせず、原因分析からAWIファイルの保存までをWI投入担当に任せる。差分外の不具合の調査、原因分析、起草原稿の検収をレビュー受領側の文脈から切り離し、セッション全体の総費用を抑えるためである。

- 要求ファイルには、`想定外事象:`行の全文と走査記録の絶対パスを要求の趣旨と`確定済みの観測`に、原因・類似見直し・対策の検討項目を`投入前に確定すべき事項`に書く。投入先は該当行が示す対象ファイルを所有するリポジトリとし、`source`属性は受領元エージェントのスキルに基づき`agent-toolkit:wi-standards`「由来と承認」で決める
- 起動した担当の`session_id`と対象の不具合を、該当レビューの引き継ぎ記録へ引き渡し済みとして対応付ける。再レビューや引き継ぎ再レビューで同じ不具合が再び報告されたら、この記録で引き渡し済みと判定し、担当を重ねて起動しない
- 投入担当の完了を待たずに、レビューの収束、統合、公開を並行して進める。投入担当の終了はバックグラウンド完了通知で確かめ、最終の作業完了報告の前までに全担当の終了を確かめる。この終了確認は統合や公開を始める前提条件にしない
- 担当が保存したAWIのファイル名は、`agent-toolkit:completion-report`の`references/report-formats.md`「作業完了報告のフォーマット」の`### 投入したWI`で報告する
- 担当から続行できない理由が返ったら、ユーザーの判断が要る事項を`agent-toolkit:user-confirmation-and-report`に従って確認する（自律モードではUWIへ退避する）。投入先リポジトリ外のファイルが対象として返った場合は、`add-wi.parent.md`「続行できない理由の扱い」に従って投入先と要求の組を確定し直す

この運用は、`agent-toolkit/rules/01-agent.md`「完遂と先送り」の「発見した独立の既存不良は同一セッション内で是正する」という一般原則の例外であり、先送りに要る事象の記録、根本原因、対応案は投入担当が保存するAWIが担保する。
ただし、本セッションに残る後続工程（レビュー修正、統合、公開）の進行を止める不具合は、同節の「進行を妨げる欠陥」としてその場で対処する。この判定は工程が実際に止まる観測に基づき、事前の詳細な原因分析を前提にしない。
その不具合は`agent-toolkit:bugfix`と同節の規定に従って直す。`process-wi`の実行中は同スキルの「即時対応」に従って当面の進行に必要な最小限の修正にとどめ、根本的な正式対応は次のセッション用のAWIへ引き継ぐ。通常の正式対応では、品質（Q）影響、レビュー対象との根本原因の同一性、処置完了の有無を判定し、判定基準は`agent-toolkit:bugfix`の`references/root-cause-analysis.md`「QとC・Dの区別」「類似見直し」「類似見直しと再発防止の完了」に従う。認可が要る場合は`agent-toolkit:user-confirmation-and-report`に従う。
レーン担当が自レーンの検証を妨げるとして報告した既存不良は、実行レビューの返却と区別し、`process-wi`の処理中は同スキルの「即時対応」として扱う。

対象の不具合ごとに、引き渡し済みの担当、是正結果、根拠付きの非該当判定、判断待ちの引き渡し先のいずれかを既存の引き継ぎ記録へ対応付けてから、レビュー収束後の工程へ進む。
不採用の記録、報告済みであること、指摘管理表の未解決件数が0件であることだけでは処置完了としない。
差分外の不具合の対応状況はレビューの未解決件数と切り離して管理し、再レビューで新たに出た事項や未処置の事項も同じ記録で追跡する。

## レビュー修正

同じレビューセッションへ再レビューを依頼する場合は、`send_message`に`instruction: 再レビューせよ`と`extra_params`を指定して送る。
入力契約で役割の継続を明示し、後述の条件に応じて必要な最新情報を加える。
組み込みの委譲機構を使う場合は、`agent-toolkit:delegation`の`references/sending.md`の生成用CLIを使う。

指摘の修正は該当worktreeを所有するエージェントが担い、起動形態ごとの担当と修正サイクルは`${CLAUDE_PLUGIN_ROOT}/share/review-loop-coordination.md`に従う。同じthreadを続ける場合は、`send_message`に`instruction: レビュー指摘の対応をせよ`を指定し、`extra_params`に`レビュー指摘管理表: <単一の絶対パスまたは全計画の表の絶対パスのJSON配列>`を渡す。「起動方法別のレビュー基準」の計画ごとの受け渡しに従い、全ての指摘管理表を1回の指示に含める。ラウンド番号は各表の記載から解決する。自由文で送る場合も同じ指示内容と名前付き入力行を使う。
同じthreadを続けられない場合は、`agent-toolkit:delegation`のSKILL.md「継続と新規起動」に従い、`exec.parent.md`の「入力」と同じ形式で新しいレーン担当を起動する。`担当種別`に`レビュー修正担当`、`再開位置`に`~/.claude/plans`配下の計画ファイルの絶対パス、`レビュー指摘管理表`に未解決の指摘を含む全計画の指摘管理表のパス（複数ならJSON配列、1つなら単一パス）を指定し、引き継ぎ記録にある全計画と各指摘管理表の対応も渡す。
指摘の採否、対象コミットの選定、修正方針、成果物・Git状態・検証結果の検収は、修正を担うエージェントが自ら行う（採否の基準は`exec.subagent.md`「実装」に、履歴統合は同書の「レビュー修正の履歴統合」に従う）。メインエージェントが指摘管理表の内容を確かめるのは、`review-loop-coordination.md`「応答済みの判定」が求める範囲にとどめる（努力目標。メインエージェントの文脈の浪費を防ぐため）。

修正担当から`対応完了`か`対応完了（再レビュー不要）`が返ったら、`検証結果`と`画面差分`の行があるかを確かめ、足りなければ再提出を求める。
全指摘を不採用として根拠と応答を記録し、採用0件となった`対応完了`も通常どおり再レビューへ進める。
修正コミットや履歴統合の有無は返却種別の判定条件にせず、未応答の指摘が0件であることを確かめる。
`対応完了`を受け取ったら、`review-loop-coordination.md`「応答済みの判定」で未応答件数が0件であることを確かめてから、同じレビューthreadへ`再レビューせよ`と`round: <ラウンド番号>`を送る。送る直前に、作業開始時から現在のHEADまでの累積差分に別の計画で認可されたコミットがあれば、「`review_contract`の生成」に従って判定対象外コミットを最新のOIDで書き直し、初回の条項も含めた契約ファイルを`review_contract: <絶対パス>`として加える。前回レビュー以降の履歴書き換えでコミットOIDが変わったら、対応表を`rewrite_map: <絶対パス>`で指定する（引き継ぎ再レビューでは`extra_params`の`rewrite_map`）。対応表は`agent-toolkit:commit`の`references/history-rewrite.md`「WI実装commitの対応の継承」が保存したJSONである。`画面差分: あり`の場合は、同じ工程で`usability-review.parent.md`に従ってユーザビリティレビューの再判定（未起動なら初回レビュー）を依頼する。
`対応完了（再レビュー不要）`を受け取ったら、`agent-basics:review-standards`の`references/reviewee.md`「方式見直しと再レビュー」の省略条件を確かめる。機械的な修正、詳細・実装レベルだけの修正、低リスク段階の追加経路のいずれかに合うかを確かめ、低リスク段階を使う場合だけtoolkit側の`reviewee.md`の判定コマンドを修正後HEADで実行する。会話履歴が途切れた後の再開、修正に関わる未達・根拠不足の再判定が要る場合、修正との因果関係を確定できない場合は、通常の`対応完了`と同じく再レビューする。
直前の返却の条件別判定行に、`exec-review-recording.md`「統合時の完成条件判定」で受理できない、修正に関わる`未達`や`根拠不足`の行が残る場合も再レビューする。
判定結果は、再判定した実行レビュー担当が更新する。

再判定が不要と確かめた場合は実行レビューを省き、次の手順で統合入力HEADを確定する。amendやautosquashでHEADのコミットOIDが変わるため、統合入力HEADと、判定行を出した時点の`レビューHEAD`は区別して保持する。

1. `画面差分: あり`で新しい画面資産が加わっている場合は、ユーザビリティレビューを省かず同じHEADに対して依頼する
2. 「応答済みの判定」で、未応答の指摘が0件であることと、指摘管理表の該当行に修正前後の短縮OIDの対応が記録されていることを確かめる
3. 専用branchのHEADを`git rev-parse --short=7`で解決し、指摘管理表にある修正後の短縮OIDと一致することを確かめる
4. その値を統合入力HEAD（`exec.parent.md`「統合の指示と受領」参照）として保持する。`lane-integration.subagent.md`「マージありの統合」のコミット一致確認はこの値で行う
5. 受領確認では、最後に実際にレビューした時点の`レビューHEAD`と突き合わせ、判定行の対象コミットを修正後のHEADへ形式的に付け替えない

## レビュー指摘管理表の保存

- 基準が`WI`のみの場合: レーン統合では`atk lane integrate`が`phase: finalized`を返した後に、直接実装では本書の受領確認と意味検収を終えた後に、指摘管理表を削除する。統合の検査と再開で表を参照するため、最終化が終わるまでは保持する。保存先はprivate-notesの管理外とする
- 基準が`CI記録`のみの場合: 同じ修正系列では同じ指摘管理表と`修正系列の開始時のHEAD`を引き継ぎ、系列の完了時に`atk plans commit ci-<修正系列の開始時のHEAD>.exec-review.tsv`で`private-notes/plans/ci/`へ保存する。保存の時機は`ci-failure-handling.md`の「入力計画」に従い、`process-wi`の公開工程では終端担当が実行する
- 基準に`計画`を含む場合: 計画を管理するエージェントが、`agent-toolkit:plan-mode`の`references/plan-file-storage.md`「計画ファイルの保存と参照」に従って計画バンドルとともに保存する

# 実行レビュー担当の起動とレビュー修正

```text
起動対象: exec-review.subagent.md
```

メインは実装後HEADの実行レビュー、修正、履歴統合を管理する。
レビュー担当の手順は`${CLAUDE_PLUGIN_ROOT}/share/exec-review.subagent.md`が定める。本書は起動、入力生成、修正、指摘管理表の保存を扱い、分類と判定は同書と`agent-toolkit:review-standards`に委ねる。

全起動で名前付き入力`未判定検証記録`を渡す。証拠要求ありでは実装担当かメインが作成したJSONの実在し読取可能な絶対パス、証拠要求なしでは`なし`とする。値は起動前に確定して渡す（レビュー担当に進捗ログや引き継ぎから探させない）。引き継ぎ再レビューでも初回値を保持する。JSONの作成手順は`${CLAUDE_PLUGIN_ROOT}/share/verification-evidence.md`「完成条件証拠の検証記録」が定め、メインが自ら実装した場合はメインが同じ手順で実装完了前に作成する。根拠にするテスト出力と手動観測は、同書「手動観測の保存」の手順で実行した版（HEADと未commit・未追跡の状態）とともに保存する。
任意入力`受理済みの完成条件証拠`へは同じ計画の進捗ログに保存した受理済み証拠のうち後続担当が読めるJSONの絶対パスを渡す（省略時と行が無い場合は`なし`）。未判定検証記録とは分け、参照を使える条件と判定は`agent-toolkit:review-standards`の`references/exec-review-recording.md`「受理済み判定への参照」に従う。
未判定検証記録では、受入シナリオの公開された呼び出し手段・入力・期待結果に対応する実結果と保存済み根拠を渡す。
最終の検証と有限の手動観測の時機は`agent-toolkit:commit`「検証」、保存済み成功と失敗限定の結果の合成は`agent-basics:check-execution`の`references/verification-scope.md`「実行条件と再実行」に従う。
委譲元所有の残工程は担当と再開先を接続し、検証済み・未実施・条件変更の判断を分けてレビューへ渡す。
根拠の十分性と要件・外部仕様の達成は独立した実行レビュー担当が判定し、判定欄の書込を所有する。

画面差分では並列画面レビュー（`${CLAUDE_PLUGIN_ROOT}/share/workflow-phases.md`「用語」）とし、ユーザビリティレビュー担当の起動と検収は`${CLAUDE_PLUGIN_ROOT}/share/usability-review.parent.md`に従う。

文章成果物の探索は`reader-fit-review.parent.md`が起動単位を所有し、通常リスクを含む集合起動の識別子をファイル別申告へ対応付けて受領する。初回・再レビュー・引き継ぎ再レビューとGit管理外・回答下書きの不足・失敗も対象・読者ごとに検収する。

## 名指し参照の読込方法

名指し参照は`read-trigger-contract.md`「名指し参照の読込方法」に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `exec-review.subagent.md` | 判断 | `read:agent-toolkit:*/share/exec-review.subagent.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../skills/review-standards/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/review-standards/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `exec.subagent.md#「実装」` | 判断 | `section:agent-toolkit:*/share/exec.subagent.md#「実装」` |
| 未判定検証記録を作成・更新する前 | `verification-evidence.md`の「完成条件証拠の検証記録」 | 判断 | `section:agent-toolkit:*/share/verification-evidence.md#「完成条件証拠の検証記録」` |
| 手動観測を保存する前 | `verification-evidence.md`の「手動観測の保存」 | 判断 | `section:agent-toolkit:*/share/verification-evidence.md#「手動観測の保存」` |
| 最終の検証と有限の手動観測の時機を判断する前 | `../skills/commit/SKILL.md#「検証」` | 判断 | `section:agent-toolkit:*/skills/commit/SKILL.md#「検証」` |
| 保存済み成功と失敗限定の結果を合成する前 | `agent-basics:check-execution`の`references/verification-scope.md#「実行条件と再実行」` | 判断 | `section:agent-basics:*/skills/check-execution/references/verification-scope.md#「実行条件と再実行」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../skills/review-standards/references/exec-review-recording.md#「受理済み判定への参照」` | 判断 | `section:agent-toolkit:*/skills/review-standards/references/exec-review-recording.md#「受理済み判定への参照」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `workflow-phases.md#「用語」` | 判断 | `section:agent-toolkit:*/share/workflow-phases.md#「用語」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `usability-review.parent.md` | 判断 | `read:agent-toolkit:*/share/usability-review.parent.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `reader-fit-review.parent.md` | 判断 | `read:agent-toolkit:*/share/reader-fit-review.parent.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../skills/delegation/references/runtime-routing.md#「工程別モデル設定」` | 判断 | `section:agent-toolkit:*/skills/delegation/references/runtime-routing.md#「工程別モデル設定」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../skills/managed-temp/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/managed-temp/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../skills/process-wi/references/immediate-fix.md#「レーンの変更範囲へ加える修正の認可」` | 判断 | `section:agent-toolkit:*/skills/process-wi/references/immediate-fix.md#「レーンの変更範囲へ加える修正の認可」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../skills/bugfix/references/ci-failure-handling.md` | 判断 | `read:agent-toolkit:*/skills/bugfix/references/ci-failure-handling.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `workflow-phases.md` | 判断 | `read:agent-toolkit:*/share/workflow-phases.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../skills/delegation/references/base-contract.md#「引き継ぎ記録先」` | 判断 | `section:agent-toolkit:*/skills/delegation/references/base-contract.md#「引き継ぎ記録先」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `review-loop-coordination.md#「ラウンド番号の保持先」` | 判断 | `section:agent-toolkit:*/share/review-loop-coordination.md#「ラウンド番号の保持先」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `reader-fit-review.parent.md#「起動」` | 判断 | `section:agent-toolkit:*/share/reader-fit-review.parent.md#「起動」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../skills/secret-files/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/secret-files/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../skills/delegation/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/delegation/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `exec.parent.md#「統合の指示と受領」` | 判断 | `section:agent-toolkit:*/share/exec.parent.md#「統合の指示と受領」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../skills/wi-standards/references/origin-and-approval.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/references/origin-and-approval.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `exec-review.subagent.md#「出力」` | 判断 | `section:agent-toolkit:*/share/exec-review.subagent.md#「出力」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../skills/review-standards/references/exec-review-recording.md#「統合時の完成条件判定」` | 判断 | `section:agent-toolkit:*/skills/review-standards/references/exec-review-recording.md#「統合時の完成条件判定」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `review-loop-coordination.md#「技術判断で外せる完成条件」` | 判断 | `section:agent-toolkit:*/share/review-loop-coordination.md#「技術判断で外せる完成条件」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `add-wi.parent.md` | 判断 | `read:agent-toolkit:*/share/add-wi.parent.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../skills/bugfix/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/bugfix/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../skills/wi-standards/SKILL.md#「由来と承認」` | 判断 | `section:agent-toolkit:*/skills/wi-standards/SKILL.md#「由来と承認」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../skills/completion-report/references/report-formats.md#「作業完了報告のフォーマット」` | 判断 | `section:agent-toolkit:*/skills/completion-report/references/report-formats.md#「作業完了報告のフォーマット」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../skills/user-confirmation-and-report/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/user-confirmation-and-report/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `add-wi.parent.md#「続行できない理由の扱い」` | 判断 | `section:agent-toolkit:*/share/add-wi.parent.md#「続行できない理由の扱い」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../skills/bugfix/references/root-cause-analysis.md#「QとC・Dの区別」「類似見直し」「類似見直しと再発防止の完了」` | 判断 | `section:agent-toolkit:*/skills/bugfix/references/root-cause-analysis.md#「QとC・Dの区別」「類似見直し」「類似見直しと再発防止の完了」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../skills/delegation/references/sending.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/sending.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `review-loop-coordination.md`の冒頭と「ラウンドの遷移」から「応答済みの判定」まで | 判断 | `section:agent-toolkit:*/share/review-loop-coordination.md#冒頭`、`section:agent-toolkit:*/share/review-loop-coordination.md#「ラウンドの遷移」から「応答済みの判定」まで` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `exec.parent.md#「入力」` | 判断 | `section:agent-toolkit:*/share/exec.parent.md#「入力」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `review-loop-coordination.md#「応答済みの判定」` | 判断 | `section:agent-toolkit:*/share/review-loop-coordination.md#「応答済みの判定」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../skills/commit/references/history-rewrite.md#「WI実装commitの対応の継承」` | 判断 | `section:agent-toolkit:*/skills/commit/references/history-rewrite.md#「WI実装commitの対応の継承」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../skills/review-standards/references/reviewee.md#「完了報告」` | 判断 | `section:agent-toolkit:*/skills/review-standards/references/reviewee.md#「完了報告」` |
| 本文が再レビュー省略の条件を確認する時 | `agent-basics:review-standards/references/reviewee.md#「方式見直しと再レビュー」` | 判断 | `section:agent-basics:*/skills/review-standards/references/reviewee.md#「方式見直しと再レビュー」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `lane-integration.subagent.md#「マージありの統合」` | 判断 | `section:agent-toolkit:*/share/lane-integration.subagent.md#「マージありの統合」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../skills/plan-mode/references/plan-file-storage.md#「計画ファイルの保存と参照」` | 判断 | `section:agent-toolkit:*/skills/plan-mode/references/plan-file-storage.md#「計画ファイルの保存と参照」` |

## 読込表

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 | 資料選択子 |
| --- | --- | --- | --- | --- |
| 完成条件証拠の検収で、計画や委譲プロンプトから複数のWI名を取得する | `agent-toolkit:wi-standards`の`references/wi-show-bulk.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/references/wi-show-bulk.md` | |

## 起動方法別のレビュー基準

レビュー基準は`計画`、`CI記録`、`WI`のうち、その実装を認可した資料が手元にあるもの全てを集合値で渡す（対応する計画があれば`計画`、対象WIがあれば`WI`、計画の無いCI失敗由来なら`CI記録`）。1件の実行レビューは`cwd`で解決する単一の対象リポジトリだけを扱い、対象リポジトリが異なる基準は別のレビューへ分ける。

対象WIが`担当モデル.実行レビュー担当`を指定した場合は、その値を起動の`model_type`と委譲入力`担当モデル`へ渡す（読者別探索担当へ引き継がれる）。比較と記録は`agent-toolkit:delegation`の`references/runtime-routing.md`「工程別モデル設定」の担当モデルの段落に従い、対象WI間で指定が異なればレビュー単位を分ける。

同じレーンに複数の計画（初回に分けた計画、凍結済みの先行計画と追加計画）がある場合は、計画ごとに同じ最終実装HEADでレビューを起動し、計画stemごとのレビュー指摘管理表と`完成条件証拠`を受け取る。各レビューへはその計画のAWI集合だけを対応付ける。
計画ごとにできる成果物は、レビュー指摘管理表、`完成条件証拠`、`review_contract`、未判定検証記録である。レーン全体を扱う担当へ渡す入力は全計画分を1回で受け取れる形にし、計画と成果物の対応を保持する。計画ごとのレビューthreadへはその計画の成果物を渡す。初回・再レビューとも、複数計画の契約は「`review_contract`の生成」で計画ごとに生成する。
レビュー修正やrebase競合の解消でHEADが変わったときは、影響する計画だけを新しいHEADで再レビューする。計画の最後の`レビューしたHEAD`を`R`、変更後のHEADを`H`、`<基点>`を`開始時のHEAD`（rebaseを経た`H`ではrebase先のOID）とし、次のどちらかに当たる計画を影響する計画とする。

- `git range-diff <開始時のHEAD>..<R> <基点>..<H>`で、その計画の判定対象commit（`review_contract`が別計画の認可commitとして外したものを除く）のいずれかが`=`以外で対応する
- `git diff --name-only <R> <H>`のファイルが、その計画の判定対象commitが変更したファイルと重なる

判定は対象worktreeで`atk run-script review-impact -- --input <入力JSONの絶対パス> --output-dir <比較記録ディレクトリの絶対パス>`を使う。入力は`version: 1`、`head`（H）、必要時の`rebase_base`、`plans`配列とし、各要素へ`plan`（計画の絶対パス）、`start_head`、`reviewed_head`（R）、レビュー時点の`commits`、メインが根拠を確かめて明示する`excluded_commits`を渡す。非0や`error`は原因を解消してから判定し直す。
影響しない計画は再レビューせず、計画・OID・理由・比較記録の所在をレーンの引き継ぎ記録へ残す。rebaseによるOID変更だけなら再レビューは不要とする。各計画のレビューが最終HEADで収束しているか、最後のレビュー後の差が影響しない修正だけであり、各表の未解決指摘が0件であることを統合の開始条件とする。

`計画`の基準は項目名`計画`で計画ファイルの絶対パスを渡す。計画の`## 実施内容`が挙げるWIは担当が自ら取得するため、委譲プロンプトへは計画へ現れないWIの記録だけを置く。
`完成条件証拠`は全ての委譲プロンプトへ含める。本文で渡すWI記録と、計画の`## 実施内容`で`人間由来のWI`か`エージェント由来のWI`として挙げる関連WIの和集合（対象WI集合）が非空の場合と、`ユーザー指示`由来の行がある場合を「証拠要求あり」と呼ぶ。証拠要求ありでは起動前にmanaged-temp（`agent-toolkit:managed-temp`）内へ計画ごとに別のJSON出力先を確保してその絶対パスを、それ以外では`なし`を渡す。起動前に要るのは親ディレクトリの実在だけであり、雛形はレビュー担当が生成する。返却の`完成条件証拠のパス`を計画名と対象WI集合へ対応付けて後続の検収へ渡す。

## レビュー指摘管理表のパス

1件の実行レビューは単一の表を使い、レビュー担当が次の保存先を解決する。

- `計画`を含む場合: 計画ファイルと同じディレクトリの`<計画stem>.exec-review.tsv`
- `WI`だけの場合: `~/.claude/plans/wi-<開始時のHEAD>.exec-review.tsv`。`開始時のHEAD`は委譲プロンプトへ記載する処理開始時のHEADの7文字以上の一意な短縮OID
- `CI記録`だけの場合: `~/.claude/plans/ci-<修正系列の開始時のHEAD>.exec-review.tsv`。`修正系列の開始時のHEAD`は委譲プロンプトへ記載する、その修正系列の開始時のHEADの7文字以上の一意な短縮OIDで、同じ修正系列では継続する

初回レビューで不在の表はレビュー担当が最初の読取前に用意するため、メインは初期化も不在を理由とした起動の拒否もしない。既存表と再レビューの表は継続し、履歴継続時に不在なら空表で補わずに原因を調べる。

## `review_contract`の生成

同じレーンに複数の計画がある場合は、「起動方法別のレビュー基準」の計画ごとの成果物の受け渡しに従い、初回の起動直前と再レビューの指示直前に、全計画を入力した`atk run-script review-contract -- --generate`で契約を計画ごとに生成して対応するレビューへ渡す。計画外の明示入力は各計画の`clauses`へ含める。単一計画では、計画から得られない条項がある初回と、別の基準で認可されたcommitが累積差分へ加わった再レビューで生成する。計画由来の条項はレビュー担当が計画から生成する。`CI記録`か計画外の`WI`を基準に含む場合は必ず渡す。

キュー外素材の逐語本文・回答全文を出典にする条項は、読取可能な原本の絶対パスと対象箇所を`content`または`source`へ記し、初回・再レビューとも同じ原本へ到達できるようにする。参照先だけで条項の内容が一意に定まる場合は、後述の参照形式を使う。

メインが条項を作成する出典は次のとおりとする。

- 基準`計画`: 計画に無い明示入力。別のレビュー基準で認可されたcommitの認可範囲を含む。`agent-toolkit:process-wi`の`references/immediate-fix.md`「レーンの変更範囲へ加える修正の認可」で同じレーンへ加えた既存不良の修正の認可（commit、対象、出所）も含む。計画に記録の無い終端区分（延期`adopt`、`終端しない`）とその後続工程・検収時機も含む。レビュー担当は計画とWI本文だけからはメインのこれらの判断を知り得ない
- 基準`CI記録`: 委譲プロンプトへ記載するCI記録（項目は`agent-toolkit:bugfix`の`references/ci-failure-handling.md`）と、`原因commitOID`の親を開始時点とした実体。入力計画は由来を示す資料であり、原因commitを計画と比べる基準にしない
- 基準`WI`: 計画へ現れない対象WIの要求、完成条件、要求単位の由来と確定した採否。`計画`を併せない場合は全対象WIと`開始時のHEAD`で特定した実体

条項は計画外のユーザー目的、現行の公開契約、ユーザー合意、認可の範囲、除外・保持、通常入力、対象外入力、誤許可と誤拒否の消費主体への影響を、独立して成否を判定できる単位へ分けて書く。集合は条件で示し、件数そのものが合否条件の場合だけ、実際に数えた件数と時点を記録する。

- 受入シナリオでは要求固有の呼び出し手段（コマンド・画面・API・hookなど）と結果を条項にし、提出されたテストと実際の観測を比べる。全体検証とCIの成功は公開工程判定（`workflow-phases.md`）に従う
- 外部可視の属性を名指しした計画や要求では、最終値だけでなく、実装が最終値を導く入力、条件分岐、省略時の値を条項にする（直接設定が一致しても導出後の値の違いを検出できるように）

海外扱いのモデルでは`regions`から最終地域区分を導く全分岐と省略時の値を確かめ、直接設定が海外でも最終値が国内なら不合格とする。権限に応じた画面表示では、バックエンドが導く権限フラグの入力と分岐、テンプレートが参照する値と最終的な表示可否を確かめる。

- 計画外の成果物には、適用する作成規範とプロジェクト規範のパスと節を出典付きで条項に加える。一意に特定できない場合は、その成果物と未特定の範囲を記録してレビュー担当が解決できる入力を残す
- 現在とは別の基準で認可されたcommitが累積差分に含まれる場合は、7文字以上の一意な短縮OID、その基準、認可根拠の計画・WI・CI記録、認可された対象範囲を独立条項にする。重複した認可判定から外すのは根拠資料で認可を確かめた範囲だけとする（commitの存在は根拠にならない）。外した変更も共有する公開契約、安全性、要求された統合結果への適合は確かめる。該当commitが無ければこの条項は省く

外部参照を持たない単一条項は`条項`、`内容`、`出典`の3行の直接契約として委譲プロンプトへ書ける。それ以外はmanaged-temp内の`review-contract.yaml`へ次の形式で保存する。

```text
version: 1
clauses:
  - clause: "<対象と判定内容>"
    content: "参照: <絶対パス>#<見出し>"
    source: "<明示入力の出所>"
```

参照先だけで内容が一意に定まる場合は`content`を`参照: <絶対パス>#<見出し>`にでき、そうでなければ契約本文を書く。commitとWIの識別子はその条項の`clause`・`content`・`source`へ書く。ASCIIのコロンと空白を含む値は引用する。
同じリポジトリの複数計画では`atk run-script review-contract -- --generate <入力JSONの絶対パス> --output-dir <出力ディレクトリの絶対パス>`で計画ごとの契約を生成する。全計画の実装commitが現在HEADに含まれるworktreeで実行する。入力は`version: 1`と`plans`配列とし、各要素へ`plan`（計画の絶対パス）、明示的な認可資料`source`、認可範囲`scope`、追加条項`clauses`（空配列可）を渡す。commit対応記録からrewrite後の現在OIDが解決される。返却JSON Linesの`plan`と`contract`を対応付け、`contract: null`は契約不要とする。認可の意味判断はメインが行い、再生成には同じ追加条項を渡し、旧OIDは生成に解決させる。
YAML契約は起動前に`atk run-script review-contract -- --contract <review-contract.yamlの絶対パス>`で構造を検証し、終了コード0と警告の不在を確かめる。委譲プロンプトの`review_contract`には直接契約か検証済みYAMLの絶対パスの一方だけを渡す。
`review_contract`は永続ファイルへ追記せず、同じ実行レビュー担当のthreadへ保持する（再レビューで更新したら更新後を保持する）。threadが継続不能と確定した場合は同じ生成元から再生成して新しい担当へ渡す。単一計画で始めて初回に省略したthreadへ、後から別の基準で認可されたcommitが累積差分に加わった場合は「レビュー修正」と同じ判定で現在のOIDの独立条項を生成する。

## 起動

実行レビュー担当は、初回起動と引き継ぎ再レビューのいずれも`agents_server`の`start`を次の引数で呼んで起動する。

| 引数 | 値 |
| --- | --- |
| `cwd` | 対象worktreeの絶対パス |
| `subagent_md_path` | `exec-review` |
| `extra_params` | 本節の入力 |
| `model_type` | `担当モデル.実行レビュー担当`の指定がある場合はその値。無ければ渡さない |

初回の`extra_params`にはレビュー基準、`完成条件証拠`、複数計画の場合か計画外の条項がある場合は生成した`review_contract`を含める。`引き継ぎ記録先`の扱い（引き継ぎ再レビューでの`（継続）`を含む）は`agent-toolkit:delegation`の`references/base-contract.md`「引き継ぎ記録先」に従う。
レーンの作業を対象とする起動では`レーン識別子`も加え、session識別名を`<レーン識別子>-exec-review`にする（`CI記録`だけの起動を除く）。起動の直前に`git merge-base <専用branch> <ベースbranch>`を解決し、計画の有無にかかわらず`開始時のHEAD`として記載する。それ以外で`WI`だけを渡す場合は処理開始時のHEADを`開始時のHEAD`とする。
`CI記録`を渡す場合は、`ci-failure-handling.md`の項目に加えて`修正系列の開始時のHEAD`と`原因commitOID`を記載する。`原因commitOID`は再帰的CI失敗ごとにCI記録と同時に更新し、`修正系列の開始時のHEAD`は同じ修正系列で継続する。CI記録はメインが原因commitの生成時に保持した情報から直接記載する。
初回は省略時の値の`レビュー種別: 初回レビュー`と`round: 1`を省く。再レビューの指示と引き継ぎ再レビューには`${CLAUDE_PLUGIN_ROOT}/share/review-loop-coordination.md`の`## ラウンド番号の保持先`の値で`round: <ラウンド番号>`を含める。
任意入力の`対象ファイル限定`を用いる場合はその対象も渡す。対象リポジトリ、レビュー対象HEAD、変更ファイル一覧、検証結果、レビュー指摘管理表などは担当が自ら解決するため、渡す必要は無い。
読者別探索の比較資料をメインが準備する場合も、次の公開操作を使える。

```text
atk run-script reader-fit-prepare -- --repository <対象worktree> --review-start <今回の開始時点> --review-head <レビュー対象HEAD> --output-dir <managed-temp内の出力先>
```

再レビューは`--previous-review-start`と`--previous-review-head`、YAML契約があれば`--review-contract`を同じレビューの値で渡し、生成済みの比較資料を再利用する。前回のYAML契約は`--previous-review-contract`へ渡し、前回値を保持する。メインが準備した資料で読者別探索を起動する場合は、`reader-fit-review.parent.md`「起動」に従って`scope_patch`を`判定範囲差分`へ、`change`の値を`修正範囲`へ渡す。

Git管理外の変更元と変更前複製の対がある場合は、レーンの引き継ぎ記録にある全対を任意入力`Git管理外の変更`で渡し、無ければ省く。メインが自ら実装した場合は、`exec.subagent.md`「実装」のGit管理外のファイルの項と同じ手順で最初の変更より前に複製を作成し、対を計画の`## 進捗ログ`（計画なしでは直接実装の引き継ぎ記録）へ残す。作業ツリー内の.env等は通常の全文差分として扱う。元ファイルが作業ツリー外にあり`agent-toolkit:secret-files`の対象となる対では、比較をキー名の集合とキーごとの値の一致・不一致に限る（同スキルの全文の読み取りと出力の禁止を守るため）。

引き継ぎ再レビューの起動では`レビュー種別: 引き継ぎ再レビュー`と次を渡す。`review_contract`は前節の継続不能時の再生成に従う。継続不能の判定と引き継ぎ手順は`agent-toolkit:delegation`のSKILL.mdの`## 継続と新規起動`に従う。

- `前回確認版`: 旧担当の引き継ぎ記録にある記録の絶対パス。前回レビューしたHEADと、Git管理外の文章成果物ごとの識別子・前回本文の複製パスを含む。記録と比較元の実在を確かめてから起動し、取得できなければ欠けたHEADか本文の取得先を委譲元へ返す
- 基準ごとの追加入力: 「起動方法別のレビュー基準」と本節の`CI記録`・`開始時のHEAD`の段落が定める値

起動前に、必須入力（`レビュー基準`、`未判定検証記録`、`完成条件証拠`、引き継ぎ再レビューでは`引き継ぎ記録先`）と基準ごとの追加入力がそろい、既存の実体を指すパスが実在し、全ての基準・開始時点・表・`review_contract`が同じ対象リポジトリに属することを確かめる。欠ける場合は起動せず、欠けた入力か条項を委譲元へ返す。

## 完成条件証拠の受領確認

実行レビュー担当が`atk run-script exec-review-evidence-check`の`--return-result`で生成した固定形式と`受領確認入力のパス`を受け取り、次を確かめる。レーン統合では、生成済み入力と受理した最後の実レビューHEADを`exec.parent.md`「統合の指示と受領」の`受領確認入力`へ引き継ぐ。Git変更前の再検査は`atk lane integrate`が行い、受領側は本節の原文・根拠の意味検収と統合時の完成条件判定を担う。

1. 返却の`レビューしたHEAD`、round、表と証拠のパス、`計画のパス`、`入力記録のパス`を同じレビューの入力として保持する。未解決件数は返却値をそのまま使い、`<配列>の判定内訳`と`達成以外の行`は非達成行の所在の一覧として使う
2. `読者別探索`のファイル別JSON申告と`レビュー開始時点`、`前回確認版の開始時点`、`前回確認版HEAD`を同じレビューの値として保持する。メイン自身が保持する回答下書きの絶対パスと、レーンの引き継ぎ記録にある`Git管理外の変更`の元ファイルのうち`.md`・`.md.tmpl`から対象集合を求め、生成済み入力の各組の必須配列`external_artifacts`（0件は`[]`）と一致することを確かめる
3. 同じ返却の`--return-result`が終了0で生成した入力には`--batch`を再実行しない。証拠・WI・計画・契約・参照先の根拠が生成後に変わった場合、または返却が固定形式でない場合だけ、対象worktreeで`atk run-script exec-review-evidence-check -- --batch <受領確認入力のパス>`を単独実行する。複数の返却は`--batch <生成済み入力の絶対パス>`を反復し、入力の結合・再編集をせず渡す。生成側が保存した`version: 1`・`reviews`は組ごとのWI・証拠・表・round・計画と入力記録の配列・読者別探索・今回と前回の開始時点とHEADを保持する。`external_artifacts`と、受領したYAML契約の絶対パスを持つ任意の`review_contract`も同じ組で渡す。契約のパスはメインが起動時に渡した値と比べる。再レビューや引き継ぎでも、その返却が生成した値を使い、再レビューを省いた修正やrebaseを理由に付け替えない。再検査した各組の診断を読み、1組でも非0なら全体を非0として扱う。Git管理下と管理外の対象集合と申告・省略の対応はこのコマンドが確かめ、不成立は同じ担当へ探索と再判定を求める
4. 未解決件数が0件で、達成必須の全行が達成していること。`--list --rows-only`で`証拠の行一覧`だけを取得できる。全行の判定・原文・`source`を行番号で対応付け、`--show-all-rows`か選択行の表示で各行の全欄と根拠全文、reviewed_headおよび参照先の入力・観測結果を読む。個別の行の読み直しは、出力を加工せずに`atk run-script exec-review-evidence-check -- <証拠の絶対パス> --list --section <配列> --row <N>`（複数行は`--row`を反復）で行う。長い出力は保存先から末尾まで読む。コマンドの成功だけで達成を受理しない
5. 再実行が非0の返却、または`達成以外の行`などが`--return-result`の固定形式である行ごとのJSONになっていない返却は、手で組み立てた返却として受理せず、報告されたWI名と不足内容を同じ担当へ返す。同じコマンドで返却を生成し直すよう求める

再レビューの各組の`previous_review_contract`がメインの保持した前ラウンドの契約と一致することを確かめる。契約の更新後も前回値を現在値へ置き換えない。

通常返却・全行表示・配列表示・選択読取とbatchでは、`exec-review-recording.md`「JSONの形式と返却前の確認」の表示契約に従って各行の根拠全文を読む。同じ出力の`証拠の根拠辞書`と順序付きの`evidence_parts`で共通段落を各行へ対応付け、`evidence_reference`は同じ出力の先行行をたどる。保存原本の全欄と根拠全文を保持し、全対象の意味と直接根拠の確認を終えて受理する。
受領確認と原文・根拠の検収を満たした証拠の絶対パスは計画の進捗ログへ記録し、後続レビューの任意入力`受理済みの完成条件証拠`はその保存先から渡す。

未応答1件以上のcompletedは、修正へ渡す正常な返却として受領する。達成必須の非達成行との整合は未応答0件の返却でだけ確かめる。全体不採用のWIでは、未応答件数によらず各条件が未達で、evidenceにreject終端と計画の実施内容の不採用根拠の所在があることを受領条件とする。

内容の検収では、対象WI集合の本文を読込表の資料の手順で取得し、完成条件と原文要求を`完成条件証拠`と比べる。原文要求は`## ユーザーコメント`、UWIの`## 回答`、完成条件節の無いAWI本文から取り、解決済みの`## ユーザー指摘の逐語引用`（`agent-toolkit:wi-standards`の`references/origin-and-approval.md`）は除く。補足のフェンス付きコードブロックは背景資料として扱う。計画の`ユーザー指示`行も要求単位に分ける（計画だけの行は`awi`が空文字列、`origin`が計画内の所在）。`user_requirements`は元のユーザー発言がある場合だけ求める。不足した要求単位は同じレビュー担当へ補完を求め、そろってから後続へ渡す。
コマンドが受理した行も、根拠が条件を直接満たすかを本文と記録で確かめる。コマンドが確かめる範囲は`exec-review.subagent.md`「出力」が定める。`達成`行は`agent-toolkit:review-standards`の`references/exec-review-recording.md`「統合時の完成条件判定」の後続工程の予定と代替観測の基準で確かめ、満たさない行は再判定を求める。
失効行ではコマンドが`source`の指すユーザー判断（`exec-review-recording.md`「失効の根拠」）かメインの技術判断の記録を確かめたことを受領条件とする。技術判断の記録として受理できるのは、`${CLAUDE_PLUGIN_ROOT}/share/review-loop-coordination.md`「技術判断で外せる完成条件」に当たる`wi_conditions`の行に限る。所在は計画確定前の実施内容の根拠か、実装着手後のレビュー指摘管理表の行とする。計画に対象AWI・条件・判断主体・外す根拠がそろえば表への転記は不要とする。それ以外の行と`review_contract`はユーザー判断の代わりにならない。否定の要求単位と不採用の根拠の対応は本文で確かめる。根拠の無い条件変更は同書「エスカレーションの事由別の扱い」へ返す。

## 返却に含まれる既存不良の受領

各ラウンドの返却と走査記録を受領した時点で、差分外・範囲外として表から外した候補と`想定外事象:`の対応を確かめる。
レーンを経由した報告にも同じ受領を適用し、メインが処置を決める。

受領したエージェントは、差分外の既存不良を示す`想定外事象:`行を受領した時点で1行を1要求とし、要求ごとに`${CLAUDE_PLUGIN_ROOT}/share/add-wi.parent.md`の専念型でWI投入担当を起動する。
このとき`agent-toolkit:bugfix`の起動と走査記録からの原因・対策の読取をせず、原因分析からAWIの保存までを担当へ任せる。
差分外の不良の調査・原因分析・原稿の検収をレビューを受領した文脈から外し、総費用を抑えるためである。

- 要求ファイルには、`想定外事象:`行の全文と走査記録の絶対パスを要求の趣旨と`確定済みの観測`へ、原因・類似見直し・対策を`投入前に確定すべき事項`へ書く。投入先は行が示す対象ファイルを所有するリポジトリとし、`source`は受領した主体のスキルから`agent-toolkit:wi-standards`「由来と承認」で決める
- 起動した担当の`session_id`と対象の不良を、同じレビューの引き継ぎ記録へ引き渡し済みとして対応付ける。再レビューと引き継ぎ再レビューの返却が同じ不良を返した場合は、この対応で引き渡し済みと判別し、担当を重ねて起動しない
- 担当の終端を待たずにレビュー収束、統合、公開を続ける。担当の終端は完了通知で回収し、完了報告の前までに全担当の終端を観測する。回収は統合と公開の開始条件に含めない
- 担当が保存したAWIのファイル名は、`agent-toolkit:completion-report`の`references/report-formats.md`「作業完了報告のフォーマット」の`### 投入したWI`で報告する
- 担当が続行できない理由を返した場合は、ユーザーだけが決める値を`agent-toolkit:user-confirmation-and-report`で確認する（自律モードではUWIへ退避する）。投入先の外の対象が返った場合は、`add-wi.parent.md`「続行できない理由の扱い」に従って投入先と要求の組を確定し直す

この扱いは`agent-toolkit/rules/01-agent.md`「完遂と先送り」の、独立した既存不良を同じセッションで是正する一般則に対する例外である。先送りに要する事象・根本原因・対応案は、担当が保存するAWIが備える。
例外として、本セッションの残りの工程（レビュー修正、統合、公開）を停止・遮断する不良は、同節の進行を妨げる欠陥としてその場で扱う。この判定は工程が停止・遮断されるという観測事実から行い、原因分析を要件にしない。
その不良は`agent-toolkit:bugfix`と同節に従って是正する。process-wiの処理中は同スキルの「即時対応」に従い、進行に必要な範囲だけ修正し、正式対応を次のセッション用AWIへ渡す。通常の正式対応では、Qへの影響、レビュー対象の問題と根本原因を同じくするかと処置の完了を、`agent-toolkit:bugfix`の`references/root-cause-analysis.md`「QとC・Dの区別」「類似見直し」「類似見直しと再発防止の完了」で判定する。認可が要る場合は`agent-toolkit:user-confirmation-and-report`に従う。
レーン担当が自レーンの検証を妨げるとして返す既存不良は実行レビューの返却に当たらず、process-wiの処理中は同スキルの「即時対応」で扱う。

対象ごとに、引き渡し済みの担当、是正結果、根拠付き非該当判定、判断待ちの受渡し先のいずれかを既存の引き継ぎ記録へ対応付けてから、レビュー収束後の工程へ進む。
不採用の記録、報告済み、未解決0件は処置完了の代わりにならない。
差分外の不良の処置はレビューの未解決件数と分け、再レビューの新着事項と未処置事項も同じ記録で追う。

## レビュー修正

同じレビューsessionの再レビューは、`send_message`へ`instruction: 再レビューせよ`と`extra_params`を渡して生成する。
役割の継続宣言を入力契約とし、後掲の条件で必要な更新情報を加える。
組み込み委譲では`agent-toolkit:delegation`の`references/sending.md`の生成CLIを使う。

指摘への修正は、そのworktreeを所有するエージェントが行う。起動方法ごとの担当と反復の調整は`${CLAUDE_PLUGIN_ROOT}/share/review-loop-coordination.md`が定める。同じthreadを継続する場合は`send_message`へ`instruction: レビュー指摘の対応をせよ`と、`extra_params`に`レビュー指摘管理表: <単一の絶対パスまたは全計画の表の絶対パスのJSON配列>`を渡す。「起動方法別のレビュー基準」の計画ごとの成果物の受け渡しに従い、全表を1回の指示に含める。ラウンドは各表から解決する。自由本文でも同じ指示と名前付き入力の行を使う。
継続できない場合は`agent-toolkit:delegation`のSKILL.mdの`## 継続と新規起動`に従い、`exec.parent.md`の`## 入力`と同じ形式で新しいレーン担当を起動する。このとき`担当種別`を`レビュー修正担当`、`再開位置`を`~/.claude/plans`の計画ファイルの絶対パス、`レビュー指摘管理表`を未解決の指摘を持つ全計画の表の絶対パスのJSON配列（1表なら単一パスも可）とする。引き継ぎ記録に累積した全計画と各表の対応も渡す。
採否、対象commit、修正方針、成果物・Git状態・検証結果の検収は修正するエージェントが担う（採否の基準は`exec.subagent.md`「実装」、履歴統合は同書「レビュー修正の履歴統合」）。メインが表を読むのは`review-loop-coordination.md`「応答済みの判定」の範囲に限る（努力目標。メインのコンテキストを保つ）。

`対応完了`か`対応完了（再レビュー不要）`を受けたら、`検証結果`と`画面差分`の行を確かめ、欠けていれば返却を求める。
全指摘を不採用として根拠と応答を記録した採用0件の`対応完了`も、通常の再レビューへ進める。
修正commitや履歴統合の有無を返却分類の条件には使わず、未応答0件を確かめる。
`対応完了`では、`review-loop-coordination.md`「応答済みの判定」で未応答件数が0件の場合だけ同じレビューthreadへ`再レビューせよ`と`round: <ラウンド番号>`を送る。送る直前に、開始時点から現在HEADまでの累積差分に別の計画で認可されたcommitが含まれるかを確かめる。含まれれば「`review_contract`の生成」の独立条項を現在のOIDで生成し、初回の条項も含めて検証してから`review_contract: <絶対パス>`を加える。前回レビュー後に履歴書換えでOIDが変わった場合は、保存された対応表を`rewrite_map: <絶対パス>`として加える（引き継ぎ再レビューでは`extra_params`の`rewrite_map`）。対応表は`agent-toolkit:commit`の`references/history-rewrite.md`「WI実装commitの対応の継承」が保存させるJSONである。`画面差分: あり`では同じ工程で`usability-review.parent.md`に従ってユーザビリティレビューの再判定（未起動なら初回レビュー）を依頼する。
`対応完了（再レビュー不要）`では、`agent-basics:review-standards`の`references/reviewee.md`「方式見直しと再レビュー」を省略条件の定義元として確認する。機械的修正、詳細・実装だけの修正、または低リスク段階の追加経路のいずれかを満たすことを確かめ、低リスク段階を使う場合だけtoolkit側`reviewee.md`の判定コマンドを修正後HEADへ適用する。会話履歴が欠けた再開、修正に関係する未達・証拠不足の再判定、修正との関係を確定できない場合は`対応完了`と同じ再レビューへ進む。
完成条件証拠があればその`wi_conditions`と`user_requirements`を読み、`exec-review-recording.md`「統合時の完成条件判定」で受理できない、修正に関係する`未達`・`証拠不足`行が残る場合も同じ再レビューへ進む。
判定値と`reviewed_head`の更新は再判定した実行レビュー担当が行う。

再判定が不要な場合は実行レビューを省き、次の順で統合入力HEADを確定する。amendとautosquashでHEADが変わるため、統合入力HEADと証拠を判定した`レビューしたHEAD`を別の値として保持する。

1. `画面差分: あり`で新しい画面資産が加わった場合は、ユーザビリティレビューを省かず同じHEADで依頼する
2. 「応答済みの判定」で未応答件数が0件で、表の該当行が修正前後の短縮OIDの対応を持つことを確かめる
3. 専用branchのHEADを`git rev-parse --short=7`で解決し、表の修正後の短縮OIDと一致することを確かめる
4. その値を統合入力HEAD（`exec.parent.md`「統合の指示と受領」）として保持する。`lane-integration.subagent.md`「マージありの統合」の一致確認はこの値で行う
5. `exec-review-evidence-check`の期待HEADには最後に実際にレビューしたHEADを使い、証拠の対象を修正後HEADへ付け替えない

## レビュー指摘管理表の保存

- `WI`だけの基準: レーン統合では`atk lane integrate`が`phase: finalized`を返した後、直接実装では本書の受領確認と意味検収の後に表を削除する。統合の検査とその再開が表を読むため、最終化まで保持する。保存先はprivate-notesの外とする
- `CI記録`だけの基準: 同じ修正系列で表と`修正系列の開始時のHEAD`を継続し、系列の終端で`atk plans commit ci-<修正系列の開始時のHEAD>.exec-review.tsv`により`private-notes/plans/ci/`へ保存する。時機は`ci-failure-handling.md`の`入力計画`の段落が定め、process-wiの公開工程では終端担当が実行する
- `計画`を含む基準: 計画を所有するエージェントが`agent-toolkit:plan-mode`の`references/plan-file-storage.md`「計画ファイルの保存と参照」に従って計画バンドルとともに保存する

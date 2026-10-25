# 実行レビュー担当の起動とレビュー修正

```text
起動対象: exec-review.subagent.md
```

メインは実装後HEADの実行レビュー、修正、履歴統合を管理する。
レビュー担当の手順は`${CLAUDE_PLUGIN_ROOT}/share/exec-review.subagent.md`が定める。本書は起動、入力生成、修正、指摘管理表の保存を扱い、分類と判定は同書と`agent-toolkit:review-standards`に委ねる。

実装担当かメインは、計画の検証コマンドと受入シナリオの実行結果を`検証結果`として返し、計画の`## 進捗ログ`（計画なしでは引き継ぎ記録）へ記録する。書き方は`${CLAUDE_PLUGIN_ROOT}/share/verification-evidence.md`「完成条件証拠の検証記録」、手動観測と全量の保存は同書「手動観測の保存」が定め、メインが自ら実装した場合もメインが同じ手順で実装完了前に記録する。レビュー担当は進捗ログか引き継ぎ記録から検証結果を読むため、起動入力へ再掲しない。
任意入力`受理済みの完成条件証拠`へは、同じ計画の進捗ログに保存した受理済みの実行レビュー返却のファイルのうち後続担当が読めるものの絶対パスを渡す（省略時と該当が無い場合は`なし`）。参照を使える条件と判定は`agent-toolkit:review-standards`の`references/exec-review-recording.md`「受理済み判定への参照」に従う。
最終の検証と有限の手動観測の時機は`agent-toolkit:commit`「検証」、保存済み成功と失敗限定の結果の合成は`agent-basics:check-execution`の`references/verification-scope.md`「実行条件と再実行」に従う。
委譲元所有の残工程は担当と再開先を接続し、検証済み・未実施・条件変更の判断を分けてレビューへ渡す。
根拠の十分性と要件・外部仕様の達成は独立した実行レビュー担当が判定し、完成条件証拠（返却の条件別判定行）の記入を所有する。

画面差分では並列画面レビュー（`${CLAUDE_PLUGIN_ROOT}/share/workflow-phases.md`「用語」）とし、ユーザビリティレビュー担当の起動と検収は`${CLAUDE_PLUGIN_ROOT}/share/usability-review.parent.md`に従う。

文章成果物の探索は`reader-fit-review.parent.md`が起動単位を所有し、通常リスクを含む集合起動の識別子をファイル別申告へ対応付けて受領する。初回・再レビュー・引き継ぎ再レビューとGit管理外・回答下書きの不足・失敗も対象・読者ごとに検収する。

## 名指し参照の読込方法

名指し参照は`read-trigger-contract.md`「名指し参照の読込方法」に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `exec-review.subagent.md` | 判断 | `read:agent-toolkit:*/share/exec-review.subagent.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../skills/review-standards/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/review-standards/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `exec.subagent.md#「実装」` | 判断 | `section:agent-toolkit:*/share/exec.subagent.md#「実装」` |
| 検証結果を記録する前 | `verification-evidence.md`の「完成条件証拠の検証記録」 | 判断 | `section:agent-toolkit:*/share/verification-evidence.md#「完成条件証拠の検証記録」` |
| 手動観測を保存する前 | `verification-evidence.md`の「手動観測の保存」 | 判断 | `section:agent-toolkit:*/share/verification-evidence.md#「手動観測の保存」` |
| 最終の検証と有限の手動観測の時機を判断する前 | `../skills/commit/SKILL.md#「検証」` | 判断 | `section:agent-toolkit:*/skills/commit/SKILL.md#「検証」` |
| 保存済み成功と失敗限定の結果を合成する前 | `agent-basics:check-execution`の`references/verification-scope.md#「実行条件と再実行」` | 判断 | `section:agent-basics:*/skills/check-execution/references/verification-scope.md#「実行条件と再実行」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../skills/review-standards/references/exec-review-recording.md#「受理済み判定への参照」` | 判断 | `section:agent-toolkit:*/skills/review-standards/references/exec-review-recording.md#「受理済み判定への参照」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `workflow-phases.md#「用語」` | 判断 | `section:agent-toolkit:*/share/workflow-phases.md#「用語」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `usability-review.parent.md` | 判断 | `read:agent-toolkit:*/share/usability-review.parent.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `reader-fit-review.parent.md` | 判断 | `read:agent-toolkit:*/share/reader-fit-review.parent.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../skills/delegation/references/runtime-routing.md#「工程別モデル設定」` | 判断 | `section:agent-toolkit:*/skills/delegation/references/runtime-routing.md#「工程別モデル設定」` |
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

同じレーンに複数の計画（初回に分けた計画、凍結済みの先行計画と追加計画）がある場合は、計画ごとに同じ最終実装HEADでレビューを起動し、計画stemごとのレビュー指摘管理表と返却を受け取る。各レビューへはその計画のAWI集合だけを対応付ける。
計画ごとにできる成果物は、レビュー指摘管理表、完成条件証拠を含む返却、`review_contract`である。レーン全体を扱う担当へ渡す入力は全計画分を1回で受け取れる形にし、計画と成果物の対応を保持する。計画ごとのレビューthreadへはその計画の成果物を渡す。初回・再レビューとも、複数計画の契約は「`review_contract`の生成」で計画ごとに書く。
レビュー修正やrebase競合の解消でHEADが変わったときは、影響する計画だけを新しいHEADで再レビューする。計画の最後の`レビューHEAD`を`R`、変更後のHEADを`H`、`<基点>`を`開始時のHEAD`（rebaseを経た`H`ではrebase先のOID）とし、次のどちらかに当たる計画を影響する計画とする。

- `git range-diff <開始時のHEAD>..<R> <基点>..<H>`で、その計画の判定対象commit（`review_contract`が判定対象外commitとして外したものを除く）のいずれかが`=`以外で対応する
- `git diff --name-only <R> <H>`のファイルが、その計画の判定対象commitが変更したファイルと重なる

判定は対象worktreeで両コマンドを直接実行する。計画の判定対象commitは、`atk run-script plan-commits -- <計画の絶対パス> --worktree <対象worktreeの絶対パス>`が返すレビュー時点のcommitから判定対象外commitを除いた集合とする。各commitのファイルは`git show --name-only --format= <commit>`で得る。終了コード0以外の実行は原因を解消してから判定し直す。
影響しない計画は再レビューせず、計画・`R`と`H`の完全OID・実行したコマンドと対応行の要点・理由をレーンの引き継ぎ記録へ残す。rebaseによるOID変更だけなら再レビューは不要とする。各計画のレビューが最終HEADで収束しているか、最後のレビュー後の差が影響しない修正だけであり、各表の未解決指摘が0件であることを統合の開始条件とする。

`計画`の基準は項目名`計画`で計画ファイルの絶対パスを渡す。計画の`## 実施内容`が挙げるWIは担当が自ら取得するため、委譲プロンプトへは計画へ現れないWIの記録だけを置く。
本文で渡すWI記録と、計画の`## 実施内容`で`人間由来のWI`か`エージェント由来のWI`として挙げる関連WIの和集合（対象WI集合）が非空の場合と、`ユーザー指示`由来の行がある場合を「証拠要求あり」と呼ぶ。証拠要求ありのレビューは返却へ条件別判定行を書く。受理した返却の`agent_message_path`（`atk agents wait`の結果が示す返却本文のファイル）を計画名と対象WI集合へ対応付けて後続の検収へ渡す。

## レビュー指摘管理表のパス

1件の実行レビューは単一の表を使い、レビュー担当が次の保存先を解決する。

- `計画`を含む場合: 計画ファイルと同じディレクトリの`<計画stem>.exec-review.tsv`
- `WI`だけの場合: `~/.claude/plans/wi-<開始時のHEAD>.exec-review.tsv`。`開始時のHEAD`は委譲プロンプトへ記載する処理開始時のHEADの7文字以上の一意な短縮OID
- `CI記録`だけの場合: `~/.claude/plans/ci-<修正系列の開始時のHEAD>.exec-review.tsv`。`修正系列の開始時のHEAD`は委譲プロンプトへ記載する、その修正系列の開始時のHEADの7文字以上の一意な短縮OIDで、同じ修正系列では継続する

初回レビューで不在の表はレビュー担当が最初の読取前に用意するため、メインは初期化も不在を理由とした起動の拒否もしない。既存表と再レビューの表は継続し、履歴継続時に不在なら空表で補わずに原因を調べる。

## `review_contract`の生成

同じレーンに複数の計画がある場合は、「起動方法別のレビュー基準」の計画ごとの成果物の受け渡しに従い、初回の起動直前と再レビューの指示直前に、計画ごとの契約を書いて対応するレビューへ渡す。計画外の明示入力は各計画の条項へ含める。単一計画では、計画から得られない条項がある初回と、別の基準で認可されたcommitが累積差分へ加わった再レビューで書く。計画由来の条項はレビュー担当が計画から生成する。`CI記録`か計画外の`WI`を基準に含む場合は必ず渡す。

キュー外素材の逐語本文・回答全文を出典にする条項は、読取可能な原本の絶対パスと対象箇所を内容か出典へ記し、初回・再レビューとも同じ原本へ到達できるようにする。参照先だけで条項の内容が一意に定まる場合は、後述の参照形式を使う。

メインが条項を作成する出典は次のとおりとする。

- 基準`計画`: 計画に無い明示入力。別のレビュー基準で認可されたcommitの認可範囲を含む。`agent-toolkit:process-wi`の`references/immediate-fix.md`「レーンの変更範囲へ加える修正の認可」で同じレーンへ加えた既存不良の修正の認可（commit、対象、出所）も含む。計画に記録の無い終端区分（延期`adopt`、`終端しない`）とその後続工程・検収時機も含む。レビュー担当は計画とWI本文だけからはメインのこれらの判断を知り得ない
- 基準`CI記録`: 委譲プロンプトへ記載するCI記録（項目は`agent-toolkit:bugfix`の`references/ci-failure-handling.md`）と、`原因commitOID`の親を開始時点とした実体。入力計画は由来を示す資料であり、原因commitを計画と比べる基準にしない
- 基準`WI`: 計画へ現れない対象WIの要求、完成条件、要求単位の由来と確定した採否。`計画`を併せない場合は全対象WIと`開始時のHEAD`で特定した実体

条項は計画外のユーザー目的、現行の公開契約、ユーザー合意、認可の範囲、除外・保持、通常入力、対象外入力、誤許可と誤拒否の消費主体への影響を、独立して成否を判定できる単位へ分けて書く。集合は条件で示し、件数そのものが合否条件の場合だけ、実際に数えた件数と時点を記録する。

- 受入シナリオでは要求固有の呼び出し手段（コマンド・画面・API・hookなど）と結果を条項にし、提出されたテストと実際の観測を比べる。全体検証とCIの成功は公開工程判定（`workflow-phases.md`）に従う
- 外部可視の属性を名指しした計画や要求では、最終値だけでなく、実装が最終値を導く入力、条件分岐、省略時の値を条項にする（直接設定が一致しても導出後の値の違いを検出できるように）

海外扱いのモデルでは`regions`から最終地域区分を導く全分岐と省略時の値を確かめ、直接設定が海外でも最終値が国内なら不合格とする。権限に応じた画面表示では、バックエンドが導く権限フラグの入力と分岐、テンプレートが参照する値と最終的な表示可否を確かめる。

- 計画外の成果物には、適用する作成規範とプロジェクト規範のパスと節を出典付きで条項に加える。一意に特定できない場合は、その成果物と未特定の範囲を記録してレビュー担当が解決できる入力を残す
- 現在とは別の基準で認可されたcommitが累積差分に含まれる場合は、そのcommitを判定対象外commitとして、完全OID、その基準、認可根拠の計画・WI・CI記録、認可された対象範囲を書く。重複した認可判定から外すのは根拠資料で認可を確かめた範囲だけとする（commitの存在は根拠にならない）。外した変更も共有する公開契約、安全性、要求された統合結果への適合は確かめる。該当commitが無ければこの項目は省く

外部参照を持たない単一条項は`条項`、`内容`、`出典`の3行の直接契約として委譲プロンプトへ書ける。それ以外は自分のセッションのmanaged-temp内のMarkdownファイルへ計画ごとに次の形式で書き、その絶対パスを渡す。

```markdown
## 条項1

- 条項: <対象と判定内容>
- 内容: <契約本文、または参照: <絶対パス>#<見出し>>
- 出典: <明示入力の出所>

## 判定対象外commit

- <完全OID>: <認可した基準と認可根拠の絶対パス、認可された対象範囲>
```

参照先だけで内容が一意に定まる場合は内容を`参照: <絶対パス>#<見出し>`にでき、そうでなければ契約本文を書く。commitとWIの識別子はその条項の各項目へ書く。判定対象外commitが無い契約では同節を省く。
同じリポジトリの複数計画では、全計画の実装commitが現在HEADに含まれるworktreeで各計画の現在のcommitを`atk run-script plan-commits -- <計画の絶対パス> --worktree <対象worktreeの絶対パス>`で取得する。他計画のcommitはその計画の判定対象外commitへ書く。認可の意味判断はメインが行い、履歴書換えの後は同じ取得で現在のOIDへ書き直し、同じ条項を保つ。
起動前に、各条項が条項・内容・出典を持ち、判定対象外commitの各OIDが`git rev-parse --verify <OID>^{commit}`で解決できることを確かめる。委譲プロンプトの`review_contract`には直接契約かMarkdownファイルの絶対パスの一方だけを渡す。
`review_contract`は同じ実行レビュー担当のthreadへ保持する（再レビューで更新したら更新後を保持する）。更新では前回のファイルを残して別のファイルへ書く。threadが継続不能と確定した場合は同じ生成元から書き直して新しい担当へ渡す。単一計画で始めて初回に省略したthreadへ、後から別の基準で認可されたcommitが累積差分に加わった場合は「レビュー修正」と同じ判定で現在のOIDの判定対象外commitを書く。

## 起動

実行レビュー担当は、初回起動と引き継ぎ再レビューのいずれも`agents_server`の`start`を次の引数で呼んで起動する。

| 引数 | 値 |
| --- | --- |
| `cwd` | 対象worktreeの絶対パス |
| `subagent_md_path` | `exec-review` |
| `extra_params` | 本節の入力 |
| `model_type` | `担当モデル.実行レビュー担当`の指定がある場合はその値。無ければ渡さない |

初回の`extra_params`にはレビュー基準と、複数計画の場合か計画外の条項がある場合は書いた`review_contract`を含める。`引き継ぎ記録先`の扱い（引き継ぎ再レビューでの`（継続）`を含む）は`agent-toolkit:delegation`の`references/base-contract.md`「引き継ぎ記録先」に従う。
レーンの作業を対象とする起動では`レーン識別子`も加え、session識別名を`<レーン識別子>-exec-review`にする（`CI記録`だけの起動を除く）。起動の直前に`git merge-base <専用branch> <ベースbranch>`を解決し、計画の有無にかかわらず`開始時のHEAD`として記載する。それ以外で`WI`だけを渡す場合は処理開始時のHEADを`開始時のHEAD`とする。
`CI記録`を渡す場合は、`ci-failure-handling.md`の項目に加えて`修正系列の開始時のHEAD`と`原因commitOID`を記載する。`原因commitOID`は再帰的CI失敗ごとにCI記録と同時に更新し、`修正系列の開始時のHEAD`は同じ修正系列で継続する。CI記録はメインが原因commitの生成時に保持した情報から直接記載する。
初回は省略時の値の`レビュー種別: 初回レビュー`と`round: 1`を省く。再レビューの指示と引き継ぎ再レビューには`${CLAUDE_PLUGIN_ROOT}/share/review-loop-coordination.md`の`## ラウンド番号の保持先`の値で`round: <ラウンド番号>`を含める。
任意入力の`対象ファイル限定`を用いる場合はその対象も渡す。対象リポジトリ、レビュー対象HEAD、変更ファイル一覧、検証結果、レビュー指摘管理表などは担当が自ら解決するため、渡す必要は無い。
メインが読者別探索を起動する場合も、Git管理下の比較資料は`reader-fit-review.parent.md`「起動」のgit参照形式で渡す。再レビューの比較元は前回のレビューHEADとし、計画別の判定範囲は同節の`判定範囲差分`で渡す。

Git管理外の変更元と変更前複製の対がある場合は、レーンの引き継ぎ記録にある全対を任意入力`Git管理外の変更`で渡し、無ければ省く。メインが自ら実装した場合は、`exec.subagent.md`「実装」のGit管理外のファイルの項と同じ手順で最初の変更より前に複製を作成し、対を計画の`## 進捗ログ`（計画なしでは直接実装の引き継ぎ記録）へ残す。作業ツリー内の.env等は通常の全文差分として扱う。元ファイルが作業ツリー外にあり`agent-toolkit:secret-files`の対象となる対では、比較をキー名の集合とキーごとの値の一致・不一致に限る（同スキルの全文の読み取りと出力の禁止を守るため）。

引き継ぎ再レビューの起動では`レビュー種別: 引き継ぎ再レビュー`と次を渡す。`review_contract`は前節の継続不能時の書き直しに従う。継続不能の判定と引き継ぎ手順は`agent-toolkit:delegation`のSKILL.mdの`## 継続と新規起動`に従う。

- `前回確認版`: 旧担当の引き継ぎ記録にある記録の絶対パス。前回レビューしたHEADと、Git管理外の文章成果物ごとの識別子・前回本文の複製パスを含む。記録と比較元の実在を確かめてから起動し、取得できなければ欠けたHEADか本文の取得先を委譲元へ返す
- 基準ごとの追加入力: 「起動方法別のレビュー基準」と本節の`CI記録`・`開始時のHEAD`の段落が定める値

起動前に、必須入力（`レビュー基準`、`引き継ぎ記録先`）と基準ごとの追加入力がそろい、既存の実体を指すパスが実在し、全ての基準・開始時点・表・`review_contract`が同じ対象リポジトリに属することを確かめる。欠ける場合は起動せず、欠けた入力か条項を委譲元へ返す。

## 完成条件証拠の受領確認

実行レビュー担当の返却を`atk agents wait`で受け取り、`agent_message_path`の返却本文を読んで次を確かめる。直接実装とレーン統合のどちらでも、受領側がこの確認と統合時の完成条件判定を担う。

1. 返却が`exec-review.subagent.md`「出力」の形式（`状態`・`レビューHEAD`・`未解決の指摘数`・`読者別探索`・開始時点と前回確認版の行、証拠要求ありでは条件別判定行）を持つ。行が欠けた返却と地の文で組み立てた返却は受理せず、不足する行を同じ担当へ返して返却し直させる
2. 返却の`レビューHEAD`を`git rev-parse <返却の値>`で解決し、レビューを依頼した対象worktreeのHEADと一致することを確かめる。一致しなければ同じ担当へ現在HEADでの再判定を求める。round、表のパス、`レビュー開始時点`、`前回確認版の開始時点`、`前回確認版HEAD`を同じレビューの値として保持する
3. 返却の`未解決の指摘数`が、`atk review-table show <表の絶対パス> --format=summary --round <round>`の`unanswered_count`と一致する
4. 証拠要求ありでは、対象WI集合の本文を読込表の資料の手順で取得し、`agent-toolkit:review-standards`の`references/exec-review-recording.md`「統合時の完成条件判定」の手順3で条件番号の過不足・重複と原文要求の対応を確かめる。不足した条件と要求単位は同じレビュー担当へ補完を求め、そろってから後続へ渡す
5. `読者別探索`のファイル別JSON申告を2つの対象集合へ対応付ける。Git管理下の集合は、`git diff --name-only --diff-filter=d <レビュー開始時点> <レビューHEAD> -- '*.md' '*.md.tmpl'`から`review_contract`の判定対象外commitだけが変えたファイルを除く。Git管理外の集合は、メイン自身が保持する回答下書きの絶対パスと、レーンの引き継ぎ記録にある`Git管理外の変更`の元ファイルのうち`.md`・`.md.tmpl`とする。各対象が起動結果の識別子か、`exec-review.subagent.md`「出力」の条件を満たす`再点検対象なし`を持つことを確かめる。`文章成果物なし`は両集合が空の場合に限る。不成立は同じ担当へ探索と再判定を求める
6. 未解決件数が0件の返却では、達成を要する全条件が`達成`であるか、区分名の付いた`未達`・`根拠不足`が統合時の完成条件判定の受理条件を満たす。各`達成`行の根拠を条件と記録・本文（リポジトリ内は`git show <レビューHEAD>:<パス>`）で確かめ、判定行の件数だけで受理しない

未応答1件以上のcompletedは、修正へ渡す正常な返却として受領する。達成必須の条件との整合は未応答0件の返却でだけ確かめる。全体不採用のWIでは、未応答件数によらず各条件が`reject`の`未達`で、根拠に計画か入力記録の実施内容の不採用根拠の所在があることを受領条件とする。

内容の検収では、対象WI集合の本文の完成条件と原文要求を返却の判定行と比べる。原文要求は`## ユーザーコメント`、UWIの`## 回答`、完成条件節の無いAWI本文から取り、解決済みの`## ユーザー指摘の逐語引用`（`agent-toolkit:wi-standards`の`references/origin-and-approval.md`）は除く。補足のフェンス付きコードブロックは背景資料として扱う。計画の`ユーザー指示`行も要求単位に分ける（計画だけの行は計画ファイル名を`<WI>`とする）。原文要求行は元のユーザー発言がある場合だけ求める。
`達成`行は`agent-toolkit:review-standards`の`references/exec-review-recording.md`「統合時の完成条件判定」の後続工程の予定と代替観測の基準で確かめ、満たさない行は再判定を求める。
`失効`の行では、根拠の所在が同書「失効の根拠」のユーザー判断かメインの技術判断の記録を指すことを読んで確かめる。技術判断の記録として受理できるのは、`${CLAUDE_PLUGIN_ROOT}/share/review-loop-coordination.md`「技術判断で外せる完成条件」に当たる完成条件行に限る。所在は計画確定前の実施内容の根拠か、実装着手後のレビュー指摘管理表の行とする。計画に対象AWI・条件・判断主体・外す根拠がそろえば表への転記は不要とする。それ以外の行と`review_contract`はユーザー判断の代わりにならない。否定の要求単位と不採用の根拠の対応は本文で確かめる。根拠の無い条件変更は同書「エスカレーションの事由別の扱い」へ返す。
受領確認と内容の検収を満たした返却の`agent_message_path`は計画の進捗ログへ`受理済みの完成条件証拠`として記録し、後続レビューの同名の任意入力はその保存先から渡す。レビュー後にHEADが変わった場合は、「起動方法別のレビュー基準」で影響しないと判定した計画を除き、新しいHEADで全条件を判定し直した返却を受領してから次へ進む。

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
差分外の不良の処置はレビューの未解決件数と分け、再レビューの新着事項と未処置事項も同じ記録で管理する。

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
`対応完了`では、`review-loop-coordination.md`「応答済みの判定」で未応答件数が0件の場合だけ同じレビューthreadへ`再レビューせよ`と`round: <ラウンド番号>`を送る。送る直前に、開始時点から現在HEADまでの累積差分に別の計画で認可されたcommitが含まれるかを確かめる。含まれれば「`review_contract`の生成」の判定対象外commitを現在のOIDで書き、初回の条項も含めた契約ファイルを`review_contract: <絶対パス>`として加える。前回レビュー後に履歴書換えでOIDが変わった場合は、保存された対応表を`rewrite_map: <絶対パス>`として加える（引き継ぎ再レビューでは`extra_params`の`rewrite_map`）。対応表は`agent-toolkit:commit`の`references/history-rewrite.md`「WI実装commitの対応の継承」が保存させるJSONである。`画面差分: あり`では同じ工程で`usability-review.parent.md`に従ってユーザビリティレビューの再判定（未起動なら初回レビュー）を依頼する。
`対応完了（再レビュー不要）`では、`agent-basics:review-standards`の`references/reviewee.md`「方式見直しと再レビュー」を省略条件の定義元として確認する。機械的修正、詳細・実装だけの修正、または低リスク段階の追加経路のいずれかを満たすことを確かめ、低リスク段階を使う場合だけtoolkit側`reviewee.md`の判定コマンドを修正後HEADへ適用する。会話履歴が欠けた再開、修正に関係する未達・根拠不足の再判定、修正との関係を確定できない場合は`対応完了`と同じ再レビューへ進む。
最後の返却の条件別判定行を読み、`exec-review-recording.md`「統合時の完成条件判定」で受理できない、修正に関係する`未達`・`根拠不足`行が残る場合も同じ再レビューへ進む。
判定の更新は再判定した実行レビュー担当が行う。

再判定が不要な場合は実行レビューを省き、次の順で統合入力HEADを確定する。amendとautosquashでHEADが変わるため、統合入力HEADと判定行を書いた`レビューHEAD`を別の値として保持する。

1. `画面差分: あり`で新しい画面資産が加わった場合は、ユーザビリティレビューを省かず同じHEADで依頼する
2. 「応答済みの判定」で未応答件数が0件で、表の該当行が修正前後の短縮OIDの対応を持つことを確かめる
3. 専用branchのHEADを`git rev-parse --short=7`で解決し、表の修正後の短縮OIDと一致することを確かめる
4. その値を統合入力HEAD（`exec.parent.md`「統合の指示と受領」）として保持する。`lane-integration.subagent.md`「マージありの統合」の一致確認はこの値で行う
5. 受領確認では最後に実際にレビューした`レビューHEAD`と比べ、判定行の対象を修正後HEADへ付け替えない

## レビュー指摘管理表の保存

- `WI`だけの基準: レーン統合では`atk lane integrate`が`phase: finalized`を返した後、直接実装では本書の受領確認と意味検収の後に表を削除する。統合の検査とその再開が表を読むため、最終化まで保持する。保存先はprivate-notesの外とする
- `CI記録`だけの基準: 同じ修正系列で表と`修正系列の開始時のHEAD`を継続し、系列の終端で`atk plans commit ci-<修正系列の開始時のHEAD>.exec-review.tsv`により`private-notes/plans/ci/`へ保存する。時機は`ci-failure-handling.md`の`入力計画`の段落が定め、process-wiの公開工程では終端担当が実行する
- `計画`を含む基準: 計画を所有するエージェントが`agent-toolkit:plan-mode`の`references/plan-file-storage.md`「計画ファイルの保存と参照」に従って計画バンドルとともに保存する

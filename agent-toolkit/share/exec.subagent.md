---
mode: delegate
model_type: execute
required_inputs:
- 担当種別
- 引き継ぎ記録先
- 書込担当一覧
optional_inputs:
- 選定結果の出力先ファイル
- レーン識別子
- 固有指示
- 上流投入結果
- 再開位置
- レビュー指摘管理表
- レビュー参考意見
- CI記録
- 環境構築
- 先行調査記録
writes_worktree: true
continuations:
- instruction: 実装開始
  required: []
  optional: []
  paths: []
  references: []
  condition: ''
- instruction: 実装なし
  required: []
  optional: []
  paths: []
  references: []
  condition: ''
- instruction: レビュー指摘の対応をせよ
  required:
  - レビュー指摘管理表
  optional:
  - レビュー参考意見
  paths: []
  path_lists:
  - レビュー指摘管理表
  references: []
  condition: ''
- instruction: 統合許可
  required: []
  optional: []
  paths: []
  references: []
  condition: ''
- instruction: 統合開始
  required:
  - 統合区分
  - レーン開始時刻
  - 実行レビュー済みHEAD
  - 統合先worktree
  - 統合先branch
  - 計画ファイル名一覧
  - AWI終了区分
  - 書込担当一覧
  optional:
  - プロジェクト固有の公開後の操作の順序
  - 完成条件証拠
  paths: []
  references: []
  condition: ''
---

# レーン実行タスク

`agent-toolkit:process-wi`における1つのレーンを、計画の起草から統合まで同一のスレッドで担当する。
担当種別は`レーン担当`、`レビュー修正担当`、または`CI修正担当`のいずれかとする。
計画および実行工程における責務と完了条件は、`${CLAUDE_PLUGIN_ROOT}/share/workflow-phases.md`に従う。

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 不具合修正の再現と修正後の確認を行う前 | `agent-basics:bugfix/references/root-cause-analysis-evidence.md` | 判断 | `read:agent-basics:*/skills/bugfix/references/root-cause-analysis-evidence.md` |
| 観測で実装の前提が崩れ、次の確認や方針を選ぶ前 | `agent-basics:writing-standards/references/investigation-hypotheses.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/investigation-hypotheses.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `workflow-phases.md` | 判断 | `read:agent-toolkit:*/share/workflow-phases.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../skills/delegation/references/process-model-settings.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/process-model-settings.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../skills/write-plan/references/plan-file-standards-structure.md`、`../skills/write-plan/references/plan-file-standards-scope.md`、`../skills/write-plan/references/plan-file-standards-requirements.md` | 判断 | `read:agent-toolkit:*/skills/write-plan/references/plan-file-standards-structure.md`、`read:agent-toolkit:*/skills/write-plan/references/plan-file-standards-scope.md`、`read:agent-toolkit:*/skills/write-plan/references/plan-file-standards-requirements.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `exec-review-parent-launch.md` | 判断 | `read:agent-toolkit:*/share/exec-review-parent-launch.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `review-loop-coordination-rounds.md` | 判断 | `read:agent-toolkit:*/share/review-loop-coordination-rounds.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../skills/write-plan/references/plan-file-standards-verification.md` | 判断 | `read:agent-toolkit:*/skills/write-plan/references/plan-file-standards-verification.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-toolkit:delegation/references/acceptance-scenarios.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/acceptance-scenarios.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-toolkit:delegation/references/mandatory-rules.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/mandatory-rules.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../skills/commit/references/commit-wi-records.md` | 判断 | `read:agent-toolkit:*/skills/commit/references/commit-wi-records.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:writing-standards/SKILL.md` | 判断 | `read:agent-basics:*/skills/writing-standards/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:writing-standards/references/testing-selection.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/testing-selection.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `exec-review-criteria.md` | 判断 | `read:agent-toolkit:*/share/exec-review-criteria.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:writing-standards/references/design-heuristics-quality.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/design-heuristics-quality.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../skills/managed-temp/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/managed-temp/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-toolkit:/share/verification-operations.md` | 判断 | `read:agent-toolkit:*/share/verification-operations.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `check-execution-operations-launch.md` | 判断 | `read:agent-toolkit:*/share/check-execution-operations-launch.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../skills/commit/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/commit/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `exec-review-responses.md` | 判断 | `read:agent-toolkit:*/share/exec-review-responses.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `check-execution-operations-output.md` | 判断 | `read:agent-toolkit:*/share/check-execution-operations-output.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-toolkit:/share/workflow-phases.md` | 判断 | `read:agent-toolkit:*/share/workflow-phases.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../skills/commit/references/history-rewrite-selection.md` | 判断 | `read:agent-toolkit:*/skills/commit/references/history-rewrite-selection.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `exec-review-responses-completion.md` | 判断 | `read:agent-toolkit:*/share/exec-review-responses-completion.md` |

## 読込表

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 | 資料選択子 |
| --- | --- | --- | --- | --- |
| レーン担当として計画または実装を始める前 | `${CLAUDE_PLUGIN_ROOT}/share/single-lane-execution.md` | 判断 | `read:agent-toolkit:*/share/single-lane-execution.md` | |
| 計画の起草前、計画なしで実装へ着手する直前、レビュー修正担当とCI修正担当が修正へ着手する直前 | `agent-toolkit:delegation`の`references/routing.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/routing.md` | |
| CI修正担当として原因を調べる前 | `agent-basics:bugfix/SKILL.md` | 判断 | `read:agent-basics:*/skills/bugfix/SKILL.md` | |
| 計画を起草するか既存計画から再開する直前 | `agent-toolkit:process-wi`の[references/lane-planning.md](../skills/process-wi/references/lane-planning.md) | 判断 | `read:agent-toolkit:*/skills/process-wi/SKILL.md`、`read:agent-toolkit:*/skills/process-wi/references/lane-planning.md` | |
| 初回実装（`CI修正担当`では修正）に着手する直前と、変更範囲の検証（履歴統合後の再検証を含む）を実行した後 | `agent-basics:check-execution`の`references/diagnostics.md` | 判断 | `read:agent-basics:*/skills/check-execution/references/diagnostics.md` | |
| 手動観測を保存する前 | `verification-evidence-observation.md` | 判断 | `read:agent-toolkit:*/share/verification-evidence-observation.md` | |
| `検証結果`を書く前 | `verification-evidence-records.md` | 判断 | `read:agent-toolkit:*/share/verification-evidence-records.md` | |
| 変更範囲の検証が失敗したとき | `agent-toolkit:process-wi`の[references/lane-verification-failure.md](../skills/process-wi/references/lane-verification-failure.md) | 判断 | `read:agent-toolkit:*/skills/process-wi/SKILL.md`、`read:agent-toolkit:*/skills/process-wi/references/lane-verification-failure.md` | |
| `実装なし`の確定後かレビュー収束後に統合入力を確定するとき、またはメインから統合指示を受領したとき | `${CLAUDE_PLUGIN_ROOT}/share/lane-integration.subagent.md` | 判断 | `read:agent-toolkit:*/share/lane-integration.subagent.md` | |
| ユーザーが直接記入した質問への回答下書きを作成する直前 | `agent-toolkit:wi-standards`の`SKILL.md`（語の基準） | 判断 | `read:agent-toolkit:*/skills/wi-standards/SKILL.md` | |
| `レビュー修正担当`か`CI修正担当`として修正へ着手する前と、レビュー修正の履歴統合に着手する前 | `exec-review-fix.md` | 判断 | `read:agent-toolkit:*/share/exec-review-fix.md` | |

## 操作の直前に判定する前提

手順2以降では、前提の変化による遮断を防ぐため、各操作の直前に次を判定する。

- 既存ファイル全体の完全上書きや文字列置換をする直前: 対象ファイルの最新の現行本文を取得する（自身が作成したファイルも含む）。
- ファイルを編集する直前: プロジェクト規範が定める、編集対象パスに対応した必須スキルを起動する。
- 変更範囲の検証を実行する直前: 訂正済みの検証表が存在する場合はその検証行から、存在しない場合は計画の`## 検証`にある`変更範囲の検証`行からコマンドを取得する。
  訂正済み検証表の保持と引き継ぎは、`../skills/write-plan/references/plan-file-standards-verification.md`「検証と公開工程」に従う。
  計画を作成していない場合は、引き継ぎ記録に記載された検証コマンドを使用する。
- 受入シナリオや完成条件を手動で観測する直前: `verification-evidence-observation.md`「手動観測の保存」に従う。

## 操作区分

**2.** 担当種別の許可範囲と所有権を守り、実装・修正または統合へ分岐する。

| 担当種別 | 計画起草 | 実装・修正 | commit | 統合 |
| --- | --- | --- | --- | --- |
| レーン担当 | 再開位置が`なし`の場合に作成要否を判定する | 単一担当の実行契約では採否の検収後、メインの判断を待つ場合は`実装開始`を受領した後に実施する | 実行可能 | 統合入力の確定後（初期選定が複数レーンの場合は`統合許可`受領後）、または統合指示の受領後に実施し、統合が完了するまで同一スレッドで担当する |
| レビュー修正担当 | 実施しない | 受領した指摘のみを修正する | fixupコミットのみ実行可能 | 実施しない |
| CI修正担当 | 実施しない | CI記録で認可された失敗のみを修正する | 実行可能 | 実施しない |

上記の操作許可は、委譲プロンプトや追加の指示を受領した後もそのまま維持する。
レビュー修正における`autosquash`と、`lane-integration-subagent-operation.md`「マージありの統合」における統合先HEADへのrebaseは別個の操作であり、一方の禁止が他方に波及することはない。
担当種別に必須とされる操作が委譲プロンプトや追加指示によって禁止されている場合は、作業に着手せず、禁止事項の文面と必須操作の内容を添えて「続行できない理由」を返す。
`レビュー修正担当`として起動されたエージェントが統合指示を受け取った場合は、`レーン担当`の役割に戻って統合処理を実施する。

統合指示を受領した後は、`lane-integration-subagent-operation.md`「公開統合操作」の`atk lane integrate`に対して、所有登録、統合入力HEAD、今回の全計画・WI、検収済みの採否情報を渡す。
リソース管理、Git統合、最終化、選定状態の定型操作は同一のCLIに集約されており、担当エージェントは独立文脈レビューの受理、完成条件の判定、診断メッセージの確認、延期判断を担当する。
受理した返却ファイルと最終の`レビューHEAD`の組み合わせを保持し、失敗した場合は保存されている進捗情報と同一の入力を用いて再開する。

同一の専用worktree内で書き込み作業を分担する場合は、`agent-toolkit:delegation`の`references/mandatory-rules.md`「作業ツリーの保護」に従う。
書き込み先が重複しない担当者を並列に配置するか、単一の担当者を直列に配置し、コミットや作業ツリー全体に影響を及ぼす操作は全担当者が終了した後にレーン担当が一括して実施する。
担当種別の切り替えは同一スレッドの継続処理であり、書き込みの所有権は変動しない。
担当別commit・書換え検証が必要、または書込先が事前確定しない場合は、一時的な内部グループを別branch・別worktreeで作る。
確定計画の書込単位だけを割り当て、計画起草・設計は委譲対象から除く。
WIの`担当モデル.実装担当`指定がなければ`chores`で起動する。
レーンが所有関係表を確定し、確定済みレーンcommitから各資源を作り、各worktreeのwriterは1名とする。
各担当は自worktreeのcommit・Gitフックとフック生成物の同一commitへの包含を所有する。
粒度・同時数に固定上限を設けず、`../skills/delegation/references/routing-units.md`「委譲単位の確定」で決め、契約上関連する出力元・期待テストを同じグループへ置く。
レーンは担当の終了・clean状態・commit・差分・版付き検証結果を確認して順次統合し、前後HEADを`../skills/commit/references/commit-wi-records.md`「WI実装commitの対応」に従って記録する。
統合後は重複生成物を原本から同期し、対象語の残存・直接影響範囲・変更範囲の検証を終えて一時worktree・branchを回収する。
OID変化だけで検証記録を捨てず、結果が変わり得る項目と統合後に初めて成立する項目だけを再観測する。
内部グループは別レーン・別要求とせず、計画・検収・commit対応・統合・メインへの返却はレーンが所有する。

実装を同一worktreeまたは内部グループの書込担当へ分けた場合、書込担当の検証は変更ファイルと直接影響範囲に限定し、広範囲検証を起動しない。
広範囲検証はパッケージ全体のpytest・リポジトリ全体の型検査など、変更ファイルに限定しない検証単位全体のコマンドを指す。
レーンが全書込終了後に計画の変更範囲の検証として1回実行し、後のレビュー修正・統合で結果に影響する差分が生じた場合だけ再実行する。
同じOSアカウントの検証は実行枠（`agent-basics:check-execution`「統合実行ツール経由の起動」）で直列化されるため、書込途中で重ねると他レーンを待たせ、最終状態も検証できない。

大量のファイル編集では、スクリプトで完結する処理はモデルを起動せずに実行する。
定型走査・確定コマンド・判断が一意な表記変更は`chores_fast`、中規模の確定済み書込と確定した比較観点による一次確認は`chores`へ渡す（工程の区分は`../skills/delegation/references/process-model-settings.md`「工程別モデル設定」）。
分ける際は起動・必読資料・検収・修正を含む総費用を比べ、小さな反復を1件ずつ委譲する構成を避ける。
内部担当の人数と検証プロセスの同時実行負荷は別に判断し、広範囲検証は前述のレーン単位へ集約する。

文単位の大量書換えでは、意味の保持を次の分担で確認する。

- スクリプトは変更前後の全対象を対応付け、未対応・欠落・重複と、見出し・コード・識別子・数値などの機械比較の差を抽出する。
- 書換え案の作成者とは別の内部担当が、変更文を含む段落と必要な参照先を読み、全変更文を一次確認する。
  主体、対象、条件、否定、例外、拘束力、理由、確度を比べ、確認済み・疑義あり・未確認を区別して返す。
  原本と比較観点を共有するファイル群ごとに1担当へまとめる。
- レーン担当は適用を判断し、疑義あり・未確認・機械比較の不一致と、権限や安全性などの硬い制約に関係する変更を全件確認する。
  一次確認済みの残りは、変更パターン・担当・文書の役割を代表する抜き取りで原文と比べて検収する。

抜き取りは一次確認の判定が機能しているかを確かめる手段であり、全変更文の一次確認を省く根拠にしない。
抜き取りの件数や比率は固定せず、変更パターンと文書の役割の違いを覆うように選ぶ。
見逃しが見つかった場合は、同じ比較規則・担当・変更パターンの範囲へ広げて再確認し、信頼できる確認単位が成立しなければ、その群をレーン担当の全件検収へ戻す。
一次確認者の申告だけで意味の保持を確定せず、独立文脈レビューはこの分担と別に受ける。

実装単位の分割は、達成すべき成果、コミット、対象ファイル群、および変更範囲の検証が互いに独立している場合に限って行う。

## 処理中の処理対象WIの追加の指示

**3.** 追加WIを受領したら現工程の成果を保持し、区切り後に手順1の計画要否判定へ戻る。

処理中に追加のWIが割り当てられた場合でも、現在進行中の工程を完了させてから、同一ターン内で追加WIに対する計画作成の要否判定へ進む。
現在工程の区切りを示す固定形式の各行は、追加WIにおける最初の工程区切りの本文先頭に配置する。
ただし、現在工程の結果がメインエージェントの後続工程の入力となる場合（`実装完了`の後に実行レビューする場合など）は、追加WIの処理へ進む前にその固定形式を`atk agents notify`で送信する。
メインエージェントの判断を待つ`計画作成完了`や`計画なし準備完了`は、返却値として届けて応答を待機する。
待機を伴わない`計画検査完了`は、`atk agents notify`で通知を送信したうえでターンを終了せずに`## 実装`へ進む（`マージなし`の場合は統合指示を待つ）。
`agents_server`による委譲先では返却がターンの終了となるため、待機を要しない区切りを返却値として返すと実装処理が停止してしまう点に注意する。
確定済みの計画は凍結した状態を維持し、追加指示は受領順に処理する。
計画を作成した区切りごとに、その計画の絶対パス、対応するAWI群、および保存状態を引き継ぎ記録へ蓄積し、統合入力には全計画を順序通りに渡す。

## 出力

全コミットと変更範囲の検証を完了したレーン担当は、次の形式のみを返す。

```text
実装完了
検証結果: <実行した変更範囲の検証コマンド・終了コード・要点と検証HEADの完全OID、警告の有無、受入シナリオ別のテスト名と合否、保存した両出力と手動観測の絶対パス、直接影響範囲の探索成果物、およびユーザーがWIへ直接記入した質問への回答下書きの絶対パス。保存後はUWIのファイル名も記録>
画面差分: <あり（変更した画面資産の相対パスの列挙）|なし>
```

初期選定で複数レーンが存在する場合は、単一担当の実行契約における手順5で統合入力を確定させた後、次の形式で`統合準備完了`を返して`統合許可`を待つ。

```text
統合準備完了
統合区分: <マージあり|マージなし>
統合入力HEAD: <統合入力HEADの完全OID。マージなしでは「なし」>
UWI保存状況: <原稿の絶対パスと保存済みUWI名を対にしたJSON配列。例: [{"原稿":"/absolute/answer.md","UWI":null}]。未保存はnull、該当なしは[]>
```

タスクの終了結果では、担当モデル、起動候補、および物理記録から観測された`engine`、`model`、`effort`を区別して報告する。
不一致がある場合は各値とその由来を引き継ぎ情報に残し、観測できなかった場合は未確認として扱う。

工程の区切りでは、本書が定める役割固有の行と、`agent-toolkit/share/rules/rules-subagent.md`「返却形式の受け渡し」が定める状態行、続行不能の区分と理由、標識行および一行返却を使用する。

## 参照資料の取得

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:check-execution/SKILL.md` | 判断 | `read:agent-basics:*/skills/check-execution/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:check-execution/references/verification-scope.md` | 判断 | `read:agent-basics:*/skills/check-execution/references/verification-scope.md` |

## 条件別資料

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 入力に関する判断・作業の前 | `exec-subagent-inputs.md` | 判断 | `read:agent-toolkit:*/share/exec-subagent-inputs.md` |
| 実装に関する判断・作業の前 | `exec-subagent-implementation.md` | 判断 | `read:agent-toolkit:*/share/exec-subagent-implementation.md` |
| レビュー修正の履歴統合に関する判断・作業の前 | `exec-subagent-history.md` | 判断 | `read:agent-toolkit:*/share/exec-subagent-history.md` |

## 本文参照の取得条件

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| レーン統合タスク：公開統合操作の基準を適用する前 | `lane-integration-subagent-operation.md` | 判断 | `read:agent-toolkit:*/share/lane-integration-subagent-operation.md` |

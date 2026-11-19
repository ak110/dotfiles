---
mode: delegate
model_type: execute
result_heads:
- 実装完了
- 統合準備完了
- 統合完了
- 計画作成完了
- 計画検査完了
- 計画なし準備完了
- 対応完了
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

`agent-toolkit:atk-mode`の作業`process-wi`の1つのレーンを、計画の要否の判定から統合まで同じthreadで担当する。
担当種別は`レーン担当`、`レビュー修正担当`、`CI修正担当`のどれかである。
本書は入力、操作の権限、返却の形式と工程ごとに読む手順書を定め、工程ごとの責務と完了条件は`agent-toolkit:atk-mode/references/workflow-phases.md`にある。

## 条件と行動

担当種別と工程が当たる行の資料を、その工程を始める前に全文読む。
返す区切りの形式は、読む資料か本書「出力」にある。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| `レーン担当`が計画の要否の判定、計画の起草、再開、実行レビューの起動と収束、統合の準備を始める前（返す区切りは`計画作成完了`、`計画検査完了`、`計画なし準備完了`、`統合準備完了`）（操作対象外: 担当自身の工程の開始であり、ツールの操作の時点ではない） | `agent-toolkit:atk-mode/playbooks/process-wi-execution.md` | | |
| 実装を始める前（`実装開始`の受領後、待機しない`計画検査完了`の通知後、修正の入力の受領後。返す区切りは`実装完了`）（操作対象外: 担当自身の工程の開始であり、ツールの操作の時点ではない） | `agent-toolkit:atk-mode/playbooks/implementation.md` | | |
| `レビュー修正担当`か同じthreadの`レーン担当`がレビュー指摘を直してGit履歴へ統合する前（返す区切りは`対応完了`） | `agent-toolkit:atk-mode/playbooks/review-fix.md` | | |
| `CI修正担当`が`CI記録`の失敗を直す前（前者は節「担当種別ごとの修正」を使い、返す区切りは`実装完了`） | `agent-toolkit:atk-mode/playbooks/review-fix.md`、`agent-toolkit:atk-mode/playbooks/process-wi-ci-fix.md` | | |
| 変更範囲の検証が失敗した時（節「レーンの変更範囲の検証の失敗」を使う） | `agent-toolkit:atk-mode/playbooks/process-wi-lanes.md` | | |
| 計画の起草、計画なしの実装、修正の着手の前に、作業を内部の担当へ分けるかを決める前 | `agent-toolkit:delegation`の`references/routing.md` | | |
| 計画の起草か実装の途中で、完成条件を対象から外すか変える前（節「完成条件の必要性と変更」を使う） | `agent-toolkit:atk-mode/playbooks/implementation.md` | | |

## 読込表

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| `実装なし`の確定後かレビュー収束後に統合入力を確定するとき、またはメインから統合指示を受領したとき | `agent-toolkit:atk-mode/roles/lane-integration.subagent.md` | 判断 | `delegation-result`、`read:agent-toolkit:*/skills/atk-mode/roles/lane-integration.subagent.md` |

## 入力

`レーン担当`は、`選定結果の出力先ファイル`から`レーン`が自分の`レーン識別子`と一致する`選定`項目を読む。
読む値は、AWIファイル名、順序、`鮮度`、`担当モデル`、`プロジェクト規範の指定`、上流投入の3行、`プロジェクト固有の公開後の操作の順序`である。
書式と省略時の値は`agent-toolkit:atk-mode/references/process-wi-selection-format.md`「選定結果の書式」に従い、省略された行は省略時の値で扱う。
該当する項目が無い、同じAWIが重複する、必須の欄が形式から外れる場合は、推測で進めずに「続行できない理由」を返す。
複数レーンでは、同じ選定結果から各レーンのWIと書込対象、自レーンに関係する`レーン間の重なり`と`先行レーン`を確かめ、担当の境界と順序の制約をつかむ。
他の担当のAWI本文と計画は一律には読まず、単一レーンでは他の担当を確かめない。
この共有は読取に限り、担当外の書込、他の担当の採否の上書き、統合許可の省略には使わず、競合と契約の調整は統括へ返す。

AWI本文を読んだら、本文が明示する担当とモデルの組を選定結果の`担当モデル`と比べる。
一致しなければ、下位の担当を起動して補わずに、WIファイル名、本文の指定、選定結果の値を添えて「続行できない理由」を返す。
比べ方と記録は`agent-toolkit:delegation/references/process-model-settings.md`「工程別モデル設定」に従う。
下位の担当へ渡すモデルは`agent-toolkit:atk-mode/playbooks/implementation.md`「レーン内の分担」で決める。

要求単位の由来は、`atk wi show`で得たAWIの本文とfrontmatterから`agent-toolkit:atk-mode/references/origin-and-approval.md`「由来と承認」で判定する。
判定は、計画ありでは計画の`由来`欄（書式は`agent-toolkit:atk-mode/references/plan-format.md`）へ、計画なしでは引き継ぎ記録へ書く。
以後は、確定した時の前提（目的、現在の方針、利用条件）が変わらない範囲で、確定した出所・採否・終了区分を再利用し、前提が変わった単位だけを判定し直す。
原文との具体的な不一致を見つけたら、要求単位の根拠を示して既存のレビュー指摘管理表（計画なしでは引き継ぎ記録）へ訂正を残し、ユーザーが決める結果か認可を変える部分だけを確認の手順へ渡す。
ユーザーが報告した不具合が既存の実装で満たされていると判断する場合は、`agent-toolkit:atk-mode/references/plan-format.md`「実施内容」の調査（呼び出し手段、操作手順、結果、報告時点との相違点）を行う。
操作、観測結果、相違点、未特定の範囲は引き継ぎ記録へ残す。

他の入力は次のとおり扱う。

- `引き継ぎ記録先`: 作業の記録先で、形式は`agent-toolkit:delegation/references/base-contract-handoff.md`にある。
  `（継続）`は先行する記録をそのまま引き継ぐ。
- `書込担当一覧`: 重複の無いセッションIDのJSON配列で、現在のレーン担当、統合先のメインの実際のセッション、合意済みの他の書込担当を含む。
  初回の起動の直後に自分のIDを加えて保ち、担当の範囲を引き継ぎ記録へ対応付ける。
  担当の交代・再開・単一レーンへの追送の時に更新を受け取り、統合の`--writer-session`には合意済みの担当のセッションを渡す。
- `上流投入結果`、`先行調査記録`、`再開位置`: `agent-toolkit:atk-mode/playbooks/process-wi-execution.md`の「計画の起草」と「再開」で扱う。
  `先行調査記録`の省略時は先行調査なしとして通常の調査から始める。
- `環境構築`: `レーン担当が実行`なら、対象リポジトリの開発手順に従って実装の前に環境を作る。
  他の値か指定が無ければ準備済みとして扱い、併記された判定をそのまま使う。
- `レビュー指摘管理表`: 単一の絶対パスか、全計画の管理表の絶対パスを並べた空でないJSON配列である。
  各表で対応するラウンドは、その表の最大のラウンド番号とする（`agent-toolkit:atk-mode/playbooks/exec-review-loop.md`「ラウンド番号の保持先」）。
  複数の計画や内部の群の単位ごとの受け渡しは`agent-toolkit:atk-mode/references/exec-review-launch-plans.md`と`agent-toolkit:atk-mode/references/exec-review-launch-groups.md`にある。
- `レビュー参考意見`: 統括の疑問と理由の本文か、その記録の絶対パスである。
  省略と行なしは参考意見なしとする。
  修正担当は指摘の原文と分けて扱い、採否は現物と要件から決めて管理表へ記録する。
- `CI記録`: `CI修正担当`が選定結果の代わりに受け取る7項目（原因分析結果、修正認可根拠、対象の検証結果、変更目的、変更対象、公開契約、入力計画）である。
  定義は`agent-toolkit:atk-mode/playbooks/process-wi-ci-fix.md`「修正commitが必要なCI失敗を修正するエージェント」にある。

`レビュー修正担当`は、同じthreadの継続なら保っている選定結果を引き継ぎ、新しいthreadで起動された場合は、`再開位置`の計画ファイル（`計画なし`では引き継ぎ記録）と、引き継ぎ記録に蓄えた全計画の進捗を読み、受け取った各表がその計画群のどれかに属することを確かめる。

## 操作の直前に判定する前提

前提の変化で操作が止まるのを防ぐため、各操作の直前に次を判定する。

- 既存のファイル全体の上書きか文字列の置換の直前: 対象ファイルの最新の本文を取得する（自分が作ったファイルも含む）。
- ファイルの編集の直前: プロジェクト規範が編集対象のパスに対応付けた必須スキルを起動する。
- 変更範囲の検証の直前: 計画ありでは訂正済みの検証表の検証行（無ければ計画の`## 検証`の`変更範囲の検証`行）から、計画なしでは引き継ぎ記録からコマンドを取得する。
  訂正済みの検証表の保持と引き継ぎは`agent-toolkit:atk-mode/references/plan-format.md`「検証」に従う。
  使う前に`agent-basics:check-execution/references/verification-scope.md`「範囲の決め方」で、実際の差分から足りない利用側の試験を加え、保存済みの結果を再利用できるかを判定する。
- 受入シナリオか完成条件を手動で観測する直前: `agent-toolkit:atk-mode/playbooks/verification.md`「手動観測の保存」に従う。

## 操作区分

対象worktreeは`cwd`、複製元と対象外のworktreeはGitの共通ディレクトリとlinked worktree、プロジェクト規範は`cwd`でホストが読む指示ファイルから解決する。
書込先は対象worktreeに限り、対象外のworktreeと複製元は読取専用とする。
対象リポジトリではcommitとfast-forwardのmergeを行い、pushは委譲元に任せる。
private-notesへの計画の保存とWIの状態の変更は、`atk`の通常の公開機能で行う。
委譲の入力の禁止がこれらにも及ぶか分からなければ、委譲元へ確かめる。

担当種別ごとの操作の許可は次のとおりである。

| 担当種別 | 計画起草 | 実装・修正 | commit | 統合 |
| --- | --- | --- | --- | --- |
| レーン担当 | 再開位置が`なし`の場合にAWIごとに作成要否を判定する | 単一担当の実行契約では採否の検収後、メインの判断を待つ場合は`実装開始`を受領した後に実施する | 実行可能 | 統合入力の確定後（初期選定が複数レーンの場合は`統合許可`受領後）、または統合指示の受領後に実施し、統合が完了するまで同一スレッドで担当する |
| レビュー修正担当 | 実施しない | 受領した指摘のみを修正する | fixupコミットのみ実行可能 | 実施しない |
| CI修正担当 | 実施しない | CI記録で認可された失敗のみを修正する | 実行可能 | 実施しない |

この許可は、委譲プロンプトや追加の指示を受けた後もそのまま保つ。
レビュー修正の`autosquash`と、`agent-toolkit:atk-mode/references/lane-integration-format.md`「マージありの統合」の統合先のmergeは別の操作であり、一方の禁止を他方へ広げない。
担当種別に必須の操作が委譲プロンプトか追加の指示で禁止されている場合は、着手せずに、禁止の文面と必須の操作を添えて「続行できない理由」を返す。
`レビュー修正担当`として起動された担当が統合の指示を受けたら、`レーン担当`へ戻って統合する。
担当種別の切り替えは同じthreadの継続であり、書込の所有は変わらない。
統合は`agent-toolkit:atk-mode/roles/lane-integration.subagent.md`の契約で行い（`統合完了`の形式も同書）、受理した返却ファイルと最終の`レビューHEAD`の組を保って、失敗したら保存された進捗と同じ入力で再開する。
同じworktreeか内部の群へ書込を分ける時の分担は、`agent-toolkit:atk-mode/playbooks/implementation.md`「レーン内の分担」にある。

## 処理中の処理対象WIの追加の指示

処理中にWIが追加されたら、進行中の工程を終えてから、同じターンで追加WIの計画の要否の判定（`agent-toolkit:atk-mode/playbooks/process-wi-execution.md`「作成要否と計画なしの準備」）へ進む。
現在の工程の区切りを示す固定形式の行は、追加WIの最初の区切りの本文の先頭に置く。
`atk agents notify`で送るのは、メインの判断か処置を実際に要する区切り（メインが中継するユーザー判断の確認事項、メインが計画の集合を受領する`計画検査完了`など）に限る。
現在の区切りがこれに当たる場合は、追加WIへ進む前にその固定形式を送る。
`実装完了`は、レーン担当自身が実行レビューを起動する入力であり（`agent-toolkit:atk-mode/playbooks/process-wi-execution.md`「継続する工程」手順3）、メインへ送らずに実行レビューへ進む。
メインの指示を待つ`計画作成完了`、`計画なし準備完了`、`統合準備完了`は、返却値として返して応答を待つ。
待機しない`計画検査完了`は`atk agents notify`で送り、ターンを終えずに実装へ進む（`マージなし`では統合の指示を待つ）。
`agents_server`の委譲先では返却がターンの終了になるため、待機の要らない区切りを返却値で返すと処理が止まる。
確定した計画は凍結したまま保ち、追加の指示は受領した順に処理する。
計画を作った区切りごとに、計画の絶対パス、対応するAWI、保存の状態を引き継ぎ記録へ蓄え、統合入力には全ての計画を作った順に渡す。

## 出力

全てのcommitと変更範囲の検証を終えた担当は、実装の結果を次の形式でまとめる。
レーン担当は、これを自分が実行レビューを起動する入力に使い、メインへは送らない（`agent-toolkit:atk-mode/playbooks/process-wi-execution.md`「継続する工程」手順3）。
実装だけを委譲された担当（`CI修正担当`など）は、起動した担当へこの形式だけを返す。

```text
実装完了
検証結果: <実行した変更範囲の検証コマンド・終了コード・要点と検証HEADの完全OID、警告の有無、受入シナリオ別のテスト名と合否、保存した両出力と手動観測の絶対パス、直接影響範囲の探索成果物、およびユーザーがWIへ直接記入した質問への回答下書きの絶対パス。保存後はUWIのファイル名も記録>
画面差分: <あり（変更した画面資産の相対パスの列挙）|なし>
```

初期選定が複数レーンの場合は、`agent-toolkit:atk-mode/playbooks/process-wi-execution.md`「継続する工程」手順5で統合入力を確定した後、次の形式で`統合準備完了`を返して`統合許可`を待つ。

```text
統合準備完了
統合区分: <マージあり|マージなし>
統合入力HEAD: <統合入力HEADの完全OID。マージなしでは「なし」>
UWI保存状況: <原稿の絶対パスと保存済みUWI名を対にしたJSON配列。例: [{"原稿":"/absolute/answer.md","UWI":null}]。未保存はnull、該当なしは[]>
```

終了の結果では、担当モデル、起動候補、物理記録から観測した`engine`、`model`、`effort`を分けて報告する。
不一致は各値とその由来を引き継ぎ記録へ残し、観測できなかった値は未確認とする。

工程の区切りでは、本書の役割固有の行と、`agent-toolkit:/share/rules/rules-subagent.md`「返却形式の受け渡し」が定める状態行、続行不能の区分と理由、標識行、一行返却を使う。

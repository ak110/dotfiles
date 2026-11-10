# レーン統合タスク：計画最終化

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 | 資料選択子 |
| --- | --- | --- | --- | --- |
| AWIの形式を判断する前 | `../skills/wi-standards/references/awi-body.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/references/awi-body.md` | |
| manifestの`verification`へ渡す検証の範囲を決める前 | `agent-basics:check-execution/references/verification-scope-selection.md` | 判断 | `read:agent-basics:*/skills/check-execution/references/verification-scope-selection.md` | |
| 省略された変更の保持とWI継承を検収する時 | `../skills/commit/references/history-rewrite-wi-records.md` | 判断 | `read:agent-toolkit:*/skills/commit/references/history-rewrite-wi-records.md` | |
| 「マージありの統合」でrebaseが競合で停止したとき、またはrebase後の変更範囲の検証が失敗したとき | `agent-toolkit:process-wi`の[references/lane-integration-failure.md](../skills/process-wi/references/lane-integration-failure.md) | 判断 | `read:agent-toolkit:*/skills/process-wi/SKILL.md`、`read:agent-toolkit:*/skills/process-wi/references/lane-integration-failure.md` | |
| `マージあり`の統合を開始する時（統合時の完成条件判定を行う時） | `exec-review-recording-integration.md` | 判断 | `read:agent-toolkit:*/share/exec-review-recording-integration.md` | |
| 「マージありの統合」の検証を実行した後 | `agent-basics:check-execution`の`references/diagnostics.md` | 判断 | `read:agent-basics:*/skills/check-execution/references/diagnostics.md` | |
| `adopt`・`reject`のメモを書く直前 | `../skills/wi-standards/SKILL.md`、`../skills/wi-standards/references/states-and-dependencies-completion.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/SKILL.md`、`read:agent-toolkit:*/skills/wi-standards/references/states-and-dependencies-completion.md` | |

## 計画最終化

**2.** 保存漏れを防ぐため、plansへ渡す最終化操作と確定値を検収する。

本節は、マニフェストのplansへ渡す操作と値の検収基準を定める。
各操作は公開統合操作から実行され、成功した操作と保存先の情報は所有登録に保持される。

受理済みの完成条件証拠が存在する計画については、返却されたファイルの絶対パスを`受理済みの完成条件証拠`として進捗ログに記録する（後続のレビューにおける参照情報として活用する）。

`計画ファイル名一覧`が`[]`の場合は、`plan-check`と計画の保存処理を省略し、後述する稼働記録、統合結果、およびAWI終了前の状態を引き継ぎ記録に残して、`保存した計画ファイル`として`[]`を返す。
予測補正記録は対象AWIの本文へ保存する。
テンプレートの本文は、`../skills/write-plan/references/plan-file-standards-progress.md`「進捗ログ」が参照する`予測補正記録`の書式に基づく。
`完了時刻=`に`{{completed_at}}`、`総経過秒=`に`{{lane_seconds}}`を指定した1件の記述とする。
保存は、`atk wi edit <AWIのファイル名> --append --body-file {{expand-file:<テンプレートの絶対パス>}} --target-repo=<統合先worktree>`で実行する。
この操作はマニフェストにおいて該当AWIの終了argvよりも前に配置する（終了済みの項目は`atk wi edit`の対象外となるため）。
引き継ぎ記録には、返却JSONの`finalization_values`の内容を転記する。
計画が存在する場合は、配列の順序に従って各計画に次の処理を適用する。

- 先行する統合処理で保存済みであり、その後に進捗の追記がない計画については操作を省略し、返却一覧には含める。
- 操作する場所は、計画のメタ情報が示す専用worktreeとする。
  すでに回収済みの場合は、引き継ぎ記録から「反映後の観測のみを行う再開」と「旧worktreeの回収」が確認でき、かつ保存時点の本文から進捗ログ以外の変更がない場合に限り、現在の専用worktreeで処理を継続する。
  それ以外の場合はメタ情報を書き換えずに「続行できない理由」を返す。
- `~/.claude/plans`に存在しない未保存の計画は、保存状態と所在を引き継ぎ記録に記録し、必要に応じて`atk plans checkout <private-notes/plans/からの相対パス>`で取得する。
- `atk run-script plan-progress --`を用いて`## 進捗ログ`に、統合区分、統合先ブランチ、`統合後のHEAD`、実行レビューの収束状況、AWI終了前の状態、およびrebaseを実行した場合は前後の短縮OIDの対応関係を追記する。
- 同じ追記処理において、レーンの稼働時間と予測補正記録を記録する。
  秒数、完了時刻、統合後のHEADは確定値のプレースホルダーを用いて渡し、手動で仮の値を入力しない。
  稼働秒数は`レーン開始時刻`からGit統合成功後・最終化開始前までの総経過秒数とし（担当者の交代があっても起点は変更しない）、書式は`../skills/write-plan/references/plan-file-standards-progress.md`「進捗ログ」に従う。
  補正前の2値、工程別または旧形式の係数、共有待ち見込みは、同節が参照する選定の記録書式に従い、保持している選定結果から取得する。
  追加WIの場合は累積予測を用い、過去の記録から取得できない値は不明と記載する。
  同一レーン内の複数の計画には同一の値を記録する。
  この記録は、次回の選定においてレーン配分の予測を算出するための基礎データとなる。
- 専用worktreeを作業ディレクトリとして`atk run-script plan-check -- --reject-migration-warnings --work-dir <計画メタ情報の対象リポジトリ> <計画ファイルの絶対パス>`を単独で実行し、終了コード0および警告がないことを確認する。
  観測のみの再開で本文の凍結を確認済みの計画については、保存時にチェックを通過しているため省略する。
- 計画ごとに`atk plans commit <計画ファイル名>`を単独で実行し、保存した計画名と結果を直ちに引き継ぎ記録へ記録する。
  途中で処理が失敗した場合は、保存済みの計画と未保存の計画を区別して報告する。
  `統合完了`の返却は、全計画の保存が完了した後にのみ行う。

## AWI行の終了

**3.** 採否とcommitを対応付け、wisへ渡す終了操作と延期項目を確定する。

本節で確定した採否と対応コミットをマニフェストのwisへ渡し、状態変更は公開統合操作から実行する。
延期の判断とその理由は引き継ぎ記録へ保存する。

計画または引き継ぎ記録で確定した採否と、受け取った終了区分を適用する。

- メモの先頭の1文は、`../skills/wi-standards/references/states-and-dependencies-completion.md`「終了」の基準（用語は同スキルの冒頭に定める基準）に従って記述する。
  `adopt`の場合は、同スキルの`../skills/wi-standards/references/awi-body-content.md`「節の構成と分担」に定めるH1直下の変更内容を、実際に実施した結果に合わせて記述する。
  `移管`行を持つ計画のAWIを`adopt`するときは、ユーザーが実施範囲を確認できるよう、後続へ分けた要求単位と移管先の登録済みWIのファイル名をメモへ書き、実施した範囲と分けた範囲を区別する。
- 採用および充足済みの項目は`atk wi adopt`、不採用の項目は`atk wi reject`で終了する。
  adoptにおけるコミットの紐付けは、`../skills/wi-standards/references/wi-standards-states.md`「状態と依存」の遷移表に従って行い、開始時や統合時のHEADを一律に機械的複製しないようにする。
  複数のコミットが存在する場合は`--commit <OID>`を繰り返し指定する。
  コミットの説明などの補足メモは`--note-file`で記録する。
- 対応コミットは、`agent-toolkit:commit`の`SKILL.md`「WI実装commitの対応」に定める取得手順に従い、計画または引き継ぎ記録と同一のstemを持つ対応記録ファイルから取得し、`--worktree`に統合後のworktreeを渡す。
- コミットを記録する採否操作では、統合先worktreeの絶対パスを`--target-repo`に渡す（同節の共通前提）。
- プロジェクト固有の公開後操作の完了まで延期する項目は状態を変更せず、該当するAWIとOIDの組をすべて返す。
- `終了しない`項目、観測のみの再開で未処理となった項目、および観測結果が完成条件を満たさなかった項目は状態を変更しない。
  `混在`の項目、ならびに後者2つの後続処理は、メインエージェントがセッション終了工程で実施する。

各状態変更コマンドは単独で実行し、処理成功の報告、対象の完全識別子、および警告が発生していないことをもって完了と判定する。
同じ状態については報告された値をそのまま使用する。

## 本文参照の取得条件

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 編集時の注意点:の基準を適用する前 | `../skills/commit/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/commit/SKILL.md` |

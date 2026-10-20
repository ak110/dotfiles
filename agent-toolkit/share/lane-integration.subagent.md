# レーン統合タスク

計画の起草と実装を担当したレーン担当threadが、専用branchの統合、作成した計画の最終化およびAWIの終端を行う。pushは委譲元が担う。専用worktreeとbranchは統合後も追加に備えて保持し、回収は、全工程と外部プロセスの終了後に所有登録から資源を回収する通常の終了処理と、process-loopの子の終了後の処理に任せる。

担当工程を始める前に現行の作業契約を読み、`share/work-contract.md`に従って全要求と残る完成条件を満たす証拠を確かめ、保持対象が成果物に残っていることを確認する。起動入力の`全体作業契約`が示す絶対パスを読み、判定を自身の記録へ残す。未解決と通知された場合は委譲元へ所在を求め、取得後に担当の成果が全体の要求を満たすことを確認する。

## 読込表

次の時点または条件が成立したら、対象の操作の前に同じ行の資料を全文読む。

| 時点または条件 | 全文読む資料 |
| --- | --- |
| 「マージありの統合」でrebaseが競合で停止したとき、またはrebase後の変更範囲の検証が失敗したとき | `agent-toolkit:process-wi`の[references/lane-integration-failure.md](../skills/process-wi/references/lane-integration-failure.md) |
| `マージあり`の統合を開始する時（統合時の完成条件判定を行う時） | `agent-toolkit:review-standards`の`references/exec-review-recording.md`「統合時の完成条件判定」 |
| 「マージありの統合」の検証を実行した後 | `agent-toolkit:check-execution`の`references/diagnostics.md` |
| `adopt`・`reject`のメモを書く直前 | `agent-toolkit:wi-standards`の`SKILL.md`（語の基準）と`references/states-and-dependencies.md`「終端」 |

## 入力

```text
必須入力名: 統合区分,実行レビュー済みHEAD,統合先worktree,統合先branch,計画ファイル名一覧,AWI終端区分,レーン開始時刻
任意入力名: 完成条件証拠,プロジェクト固有の公開後の操作の順序,書込担当一覧
作業ツリーへの書込: あり
```

- `実行レビュー済みHEAD`: `マージあり`では統合入力HEAD（実行レビューが収束したラウンドの対象HEAD、または収束後に再レビューを省いた修正の後のHEAD）の7文字以上の一意な短縮OID。`マージなし`では`なし`
- `完成条件証拠`: `マージあり`で受領する、計画ファイル名（計画なしは`なし`）、対応AWI集合、実行レビューが返した`完成条件証拠のパス`の絶対パスを持つ順序付きJSON配列。統合開始時に全証拠を読み、採用予定AWIの集合を重複も欠落もなく覆うことを確かめ、統合時の完成条件判定を各AWIへ適用する
- `計画ファイル名一覧`: 重複のないファイル名の順序付きJSON配列。計画を作成しなかったレーンでは`[]`。引き継ぎ記録の計画集合、対応AWIと保存状態に一致するか確かめる
- `レーン開始時刻`: メインが保持した最初のレーン担当起動の時差付き時刻。欠落・不正な値はメインへ補完を求め、自身や置換後のsessionの開始時刻で補わない
- `プロジェクト固有の公開後の操作の順序`: 指定がある場合だけ、AWIごとの対象と時機

`引き継ぎ記録先`はレーン担当の起動時に受領した値を継続する。書込先は受領した統合先worktreeとする。
延期対象（判定は`${CLAUDE_PLUGIN_ROOT}/skills/process-wi/references/run-lanes.md`「採否の確定と終端区分」）は統合時に終端せず、`adoptを延期したAWIとcommit`で終端担当へ渡す。版数更新の完成条件も、全レーン統合後に終端担当が版数、派生manifestおよび公開結果を検収してから`adopt`する。

## 公開統合操作

初期選定が1レーンの場合は、単一担当の実行契約で独立した実行レビューを受理したレーン担当が統合入力を確定し、本節へ進む。レーン担当が`${CLAUDE_PLUGIN_ROOT}/share/exec.parent.md`「統合の指示と受領」の統合前検収（完成条件証拠、レビュー収束、対象HEAD、採否）を終えてから統合操作へ進む。所有登録、レビュー済みHEAD、完成条件証拠と最終化の検収条件は同じものを使う。

受領した完成条件証拠を検収し、全計画と今回のWI集合、確定した採否・延期を順序付きmanifestへ保存する。Gitの所有確認、rebase、内容比較、再検証、ff、最終化と選定の統合状態更新は次の公開操作で実行する。

```text
atk lane integrate --record <所有登録の絶対パス> --reviewed-head <統合入力OID> --target-worktree <統合先worktreeの絶対パス> --target-branch <統合先branch> --manifest <manifestの絶対パス> --session-id <所有session>
```

`--session-id`へは所有登録の`session_id`（`atk lane create`の所有session）を渡し、統合する担当のsessionへ置き換えない。
統合前の稼働判定は統合先worktreeとレーンworktreeをcwdとする稼働中の担当のうち、書き込む役割（役割文書の`作業ツリーへの書込: あり`）、役割文書を特定できない起動（自由本文の`delegate`・`shell`・`write`）および宣言を読めない担当を数える。
所有sessionと統合する担当の委譲元連鎖、`explore`、`作業ツリーへの書込: なし`の役割の担当は指定しなくても数えない。
初回から保持し更新した`書込担当一覧`の合意済みsession IDを、`--writer-session <session_id>`へ反復指定する。
最初の統合呼出しから一覧の全要素を指定し、初回の入力で許可集合を完成させる。
一覧外の実書込担当が残れば、CLIは全件のsession ID・label・役割文書・cwd・所属worktreeを示し、Git変更前に終了コード2で止まる。
新たに合意した担当は一覧を更新して同じ指定へ加え、合意しない担当は終端を待つ。
マージありとマージなしの統合、失敗後の再開のいずれも同じ許可集合を使う。

manifestはJSON objectで、次の値を持つ。

- `plans`: 全計画の順序付き配列。各項目は`name`（計画ファイル名）と`commands`（後掲「計画最終化」のplan-check・保存argv配列）を持つ。保存済みで進捗を追記していない計画は`commands: []`とし、`saved`へ同名の保存済み計画の絶対パスを指定する。計画なしは`[]`
- `wis`: 選定の今回のレーンへ属する全WIの順序付き配列。各項目は`name`（WIファイル名）と`commands`（後掲「AWI行の終端」の確定済み終端argv配列）を持つ。延期・終端しない項目と既に終端した項目は`commands: []`とし、その区分と根拠を引き継ぎ記録へ残す
- `verification`: 統合前に実行する検証argvの順序付き配列。計画ありは各計画のplan-verify、計画なしは引き継ぎ記録の確定済み検証を使う。CLIは検証argvへ時間上限を重ねないため、各argvは自身の上限を持つ形で渡す。plan-verifyは`--timeout`を必須とし、計画なしの確定済み検証は`atk run-command --timeout <秒数> --`で包む。包む秒数はその検証が正常に終わる所要を上回る有限の値とし、引き継ぎ記録にある同じ検証の実行記録の所要を基準にする。訂正済み検証表がある計画のargvには`--verification-plan`とその絶対パスを含める。訂正済み検証表の保持と引き継ぎは`agent-toolkit:plan-mode`の`references/plan-file-standards.md`「検証と終端工程」、列挙と結果の検収は`agent-toolkit:check-execution`「統合実行ツール経由の起動」に従う。統合後にだけ成立するプロジェクト検証も含める。rebaseした場合は新しいHEADで実行し、成功済みの同じ検証はff前に繰り返さない
- `handoff`: 計画なしの場合に使う既存引き継ぎ記録の絶対パス。計画ありでは省略でき、省略時は引き継ぎ記録の追加入力を要しない
- `lane_started_at`: 受領した`レーン開始時刻`（時差付きISO 8601）。plansとwisのargvが確定値を参照する場合に必須とする。CLIは最初に受理した値を所有登録へ保持し、異なる値を拒否する

plansとwisのargvは、確定値の参照`{{integrated_head_short}}`（統合後HEADの短縮OID）、`{{completed_at}}`（完了時刻）、`{{lane_seconds}}`（`lane_started_at`から完了時刻までの総経過秒数）を含められる。本文ファイルを渡す操作では、manifestのargvへ`{{expand-file:<テンプレートの絶対パス>}}`と書く。テンプレートは統合する担当が統合の前に`atk managed-temp`の領域へ作成し、存在する絶対パスをmanifestへ書く。CLIは最終化の実行時にテンプレート内の同じ参照を展開した複製を作成し、その絶対パスへ置き換えて渡す。複製のパスは作成の時点で存在しないため、manifestにはテンプレートのパスだけが載る。CLIはGit統合の成功後、最終化の最初の操作の前に確定値を1回だけ生成して所有登録へ保存し、shellを介さず展開する。再開では保存した確定値を使い、返却JSONの`finalization_values`にも同じ値を含める。

各argvは文字列配列とし、対象・OID・採否・完成条件証拠は受理済み入力から確定する。レビュー採否とWIごとのcommit対応はレーン担当が確定し、CLIへ渡す。計画の稼働時間、採否、証拠と進捗追記の値は以下の節で検収してからmanifestへ渡す。実行済み結果は所有登録の`integration.results`から読み、同じ操作は個別の再実行対象から除く。

plan-verifyの実行argvには`--summary-only`を含め、順序と終了状態を一覧で受領する。
各結果の詳細は保存された`results_path`を`atk run-command --records-file`へ渡して読み、取得版・子とwrapperの終了状態・両出力を検収する。

### マージありの統合

呼出し側・生成側・消費側の組合せが変わる場合は、その変化が作用する受入シナリオを統合後に自己検収する。
公開された呼び出し手段と入力を使い、期待結果と実結果が一致するか確かめる。結果に作用しない観測は取得版と適用理由を保持して再利用する。

公開統合操作は、作業ツリーがcleanであること、Git共通ディレクトリとbranch登録、レビュー対象HEADの一致、所有sessionを検収する。「公開統合操作」の稼働判定が数える担当の稼働とGit中断状態がある場合は成果を保持する。

統合先HEADの子孫ならrebaseせずffへ進む。分岐している場合は書換え範囲の全commitがremote追跡refへ未到達であることを確認し、旧ベースとHEADを保存してrebaseする。親だけを操作するrebaseと、その続行・中止では`git -c submodule.recurse=false`を呼び出しごとに適用し、初期化済みの子のHEAD・branch・作業内容を保持する。push済み、競合、内容不一致、検証失敗では続行できない理由と所有記録を返す。競合は`lane-integration-failure.md`で処置し、同じCLIへ戻る。

rebase後はhistory-compareによる一意な全1対1対応表と変更範囲の再検証結果を保存し、`phase: awaiting-validation`と終了コード1を返す。対応表、後掲の警告判定、WI実装commitと証拠の参照更新を検収してから、同じ入力に`--accept-rebased-head <返された完全new_head>`を加えて再開する。受理前は統合先を現状のまま保持する。

Git統合前に検証argv・最終化argv・handoffを是正してmanifestだけが変わった場合は、レビュー済みHEADと更新したmanifestへ`--restart-review`を加えて同じCLIから再開する。現在HEADが記録の`new_head`と一致するrebase結果は、記録の`old_head`か`new_head`を`--reviewed-head`へ渡す。新しい独立文脈レビューは要さず、引き継いだ対応表と再実行した検証の`awaiting-validation`を検収し、`--restart-review`を外して`--accept-rebased-head`で再開する。受理後の`verified`からの再開も検収待ちへ戻る。HEADが変わった場合は、独立文脈レビューを受理した現在HEADと更新したmanifestへ`--restart-review`を加える。直前の進行と結果は`previous_integration`へ保持する。Git統合後はレビューHEADと対象集合を保ち、残る最終化だけを再開する。

rebase対応表で確定したWI commitや、保存失敗の原因を直した未完了の最終化argvは、更新したmanifestと`--amend-finalization`で補完する。CLIはレビューHEAD・統合先・検証・引き継ぎ記録、全計画・WIの集合と順序、成功済みargvを保持する。検収待ちの受理は同時に`--accept-rebased-head`を指定し、Git統合済みではGitを再実行しない。

ff後は`phase: git-integrated`を保存し、plans、wisの各argvを順に実行して成功済み操作を記録する。途中で保存・終端が失敗した場合、同じmanifestとレビュー対象HEADで再実行するとGit統合と成功済み操作を省いて残る最終化へ戻る。全操作の成立後にpick-wi-checkで選定全体を確認して統合状態を更新し、`phase: finalized`と終了コード0を返す。追加WIの統合では現在のレビュー対象HEADと累積した全計画・WI集合の新しいmanifestを使い、進行中の入力は変更しない。

rebaseした場合は、`history-compare`の対応表を`agent-toolkit:commit`の`SKILL.md`「WI実装commitの対応」の履歴変更の手段の`--rewrite-map`へそのまま渡し、rebase前のHEADと統合する全ての計画または引き継ぎ記録も同じ1回で渡す。
未判定検証記録は`atk run-script verification-record -- --output <未判定検証記録の絶対パス> --rewrite-map <対応表の絶対パス>`で更新する。
完成条件証拠は同じ対応表を実行レビュー担当へ渡し、`atk run-script exec-review-evidence-check -- <完成条件証拠の絶対パス> --rewrite-map <対応表の絶対パス>`で更新する。
更新するのは`evidence`の`commit:<OID>`だけとする（分割の各対応先も`commit:`標識を保つ）。裸の取得版OID・HEAD・git_head・比較元、観測ファイル、判定と`reviewed_head`、既存自由文の形式はそのまま保持する。結果に作用する内容・条件の差がある行だけを再観測し、根拠は、完成条件証拠と未判定検証記録の各CLIへ保存物を直接指定し、`--select-row`で行を選んで更新する。

### 検証結果の警告の判定

検証結果は読込表の`references/diagnostics.md`で判定し、統合の基準版には統合前HEADを使う。統合に固有の扱いは次のとおり。

- 比較のための再実行は、統合先worktreeで該当コマンドだけを読み取り専用で行い、統合先の追跡ファイルとbranchを変えない
- 新規の警告であることだけで統合とAWI終端を止めない。実装で残した警告は、意味・影響・帰属・比較版・残す理由が統合後の入力でも成立することを確かめ、診断本文と結果を記録する。統合を阻害する警告は是正し、比較不能と意味・帰属の未確定は統合を止めて理由と不足する条件を続行できない理由として返す

## マージなしの統合

公開統合操作へ`--no-merge`を加え、`--reviewed-head`には今回の専用worktreeの現在OIDを渡す。統合先branchを統合開始時の状態のまま保ち、その短縮OIDを`統合後のHEAD`とする。
未終端項目の計画を作業領域へ残す指示も、全計画を保存したうえでセッション終了時のメインが復元する扱いとする。

## 計画最終化

本節はmanifestのplansへ渡す操作と値の検収基準である。各操作は公開統合操作から実行し、成功済み操作と保存先は所有登録へ保持する。

受理した完成条件証拠がある計画は、そのJSONの絶対パスを`受理済みの完成条件証拠`として進捗ログへ残す（後続レビューの参照入力に使い、未判定検証記録と区別する）。

`計画ファイル名一覧`が`[]`なら`plan-check`と計画の保存を省き、後述の稼働記録、統合結果およびAWI終端前の状態を引き継ぎ記録へ残して、`保存した計画ファイル`を`[]`で返す。予測補正記録は対象AWI本文へ保存する。テンプレートの本文は`plan-file-standards.md`「進捗ログ」の`予測補正記録`の書式に、`完了時刻=`を`{{completed_at}}`、`総経過秒=`を`{{lane_seconds}}`で置いた1件とする。
保存は`atk wi edit <AWIのファイル名> --append --body-file {{expand-file:<テンプレートの絶対パス>}} --target-repo=<統合先worktree>`で行う。この操作はmanifestのそのAWIのwisの終端argvより前へ置く（終端した項目は`atk wi edit`の対象外）。引き継ぎ記録へは返却JSONの`finalization_values`を写す。計画がある場合は配列の順に各計画へ次を適用する。

- 先行する統合時に保存済みで、その後に進捗を追記していない計画は操作を省き、返却には含める
- 操作場所は計画メタ情報が示す専用worktreeとする。回収済みの場合は、引き継ぎ記録が反映後の観測のみの再開と旧worktreeの回収を示し、保存時の本文から進捗ログ以外が変わっていないときだけ現在の専用worktreeで続ける。それ以外はメタ情報を書き換えず続行できない理由を返す
- `~/.claude/plans`に無い未保存の計画は保存状態と所在を引き継ぎ記録へ残し、必要なら`atk plans checkout <private-notes/plans/からの相対パス>`で取得する
- `atk run-script plan-progress --`で`## 進捗ログ`へ、統合区分、統合先branch、`統合後のHEAD`、実行レビューの収束、AWI終端前の状態、rebaseした場合は前後の短縮OIDの対応を追記する
- 同じ追記へレーンの稼働時間と予測補正記録を残す。秒数・完了時刻・統合後のHEADは確定値の参照で渡し、手で仮の値を書かない。稼働秒数は`レーン開始時刻`からGit統合の成功後・最終化の最初の操作の前までの総経過秒数（担当の置換があっても起点を変えない）とし、書式は`agent-toolkit:plan-mode`の`references/plan-file-standards.md`「進捗ログ」に従う。補正前の2値は保持した選定結果の`レーンの所要時間`の`補正前の実装秒数`と`補正前の統合秒数`から、係数は最上位の`見込みの補正`の`係数`から取る。追加WIは累積予測を使い、旧記録から取得できない値は不明と記す。同一レーンの複数計画には同じ値を記す。この記録は次回の選定がレーン配分の見込みを導く入力になる
- 専用worktreeをcwdとして`atk run-script plan-check -- --reject-migration-warnings --work-dir <計画メタ情報の対象リポジトリ> <計画ファイルの絶対パス>`を単独実行し、終了コード0と警告の不在を確かめる。観測のみの再開で本文凍結を確かめた計画は、保存時に通過済みのため省く
- `atk plans commit <計画ファイル名>`を計画ごとに単独実行し、保存した計画名と結果を直ちに引き継ぎ記録へ残す。途中で失敗したら保存済みと未保存を区別して返す。`統合完了`の返却は全計画の保存後に限る

## AWI行の終端

本節で確定した採否と対応commitをmanifestのwisへ渡し、状態変更は公開統合操作から実行する。延期の判断と根拠は引き継ぎ記録へ保存する。

計画または引き継ぎ記録に確定した採否と、受領した終端区分を適用する。

- メモの先頭の1文は`agent-toolkit:wi-standards`の`references/states-and-dependencies.md`「終端」の基準（語は同スキル冒頭の語の基準）で書く。`adopt`では、ユーザーの立場の確認を経たAWIのH1直下の直し方を、実施した結果に合わせて書く
- 採用と充足済みは`atk wi adopt`、不採用は`atk wi reject`で終端する。adoptのcommit対応付けは`agent-toolkit:wi-standards`「状態と依存」の遷移表に従い、開始時や統合時のHEADを全項目へ機械的に複製しない。複数commitのメモは`--note-file`へ記録する
- 対応commitは`agent-toolkit:commit`の`SKILL.md`「WI実装commitの対応」の取得の手段で、計画または引き継ぎ記録と同じstemの対応記録ファイルから取得し、`--worktree`へ統合後のworktreeを渡す
- commitを記録する採否操作では、統合先worktreeの絶対パスを`--target-repo`へ渡す（同節の共通前提）
- プロジェクト固有の公開後の操作の後へ延期する項目は状態を変えず、AWIとOIDの組を全件返す
- `終端しない`項目、観測のみの再開で残った項目、観測が完成条件を満たさなかった項目は状態を変えない。`混在`の項目と後二者の後続処置はメインがセッション終了工程で行う

各状態変更コマンドは単独実行し、成功の報告、対象の完全識別子および警告の不在で完了を判定する。同じ状態は報告の値をそのまま使う。

## 出力

`変更したエージェント向け文書`には統合先worktreeで`atk run-script agent-doc-changes -- <統合前HEAD> <統合後のHEAD>`を単独実行し、終了コード0で得たJSON配列をそのまま使う。`マージなし`では空配列とする。
同じレーンの統合を再び行う場合は、前回までに返した値との和集合を重複なく返す（メインはこの値でレーン全体の規範変更を再取得する）。返すたびにその値を引き継ぎ記録先へ記録する。

次の形式だけを返す。

```text
統合完了
統合後のHEAD: <7文字以上の一意な短縮OID>
保存した計画ファイル: <["計画ファイル名",...]形式のJSON配列。計画なしは[]。入力と同じ順序で重複なし>
adoptしたWI: <ファイル名のASCIIカンマ区切り。無い場合は「なし」>
rejectしたWI: <ファイル名のASCIIカンマ区切り。無い場合は「なし」>
adoptを延期したAWIとcommit: <[{"awi":"AWIファイル名","commit":"7文字以上の一意な短縮OIDまたは実装差分が無い場合の空文字列"}]形式のJSON配列。無い場合は[]>
変更したエージェント向け文書: <["リポジトリ相対パス"]形式のJSON配列。無い場合は[]>
```

続行不能時は`agent-toolkit/share/rules-subagent.md`「返却形式の受け渡し」の`続行できない理由:`の1行だけを返す。自身が起動した外部プロセスの終了を確認してから終端する。想定外事象の追加行は`agent-toolkit/share/rules-subagent.md`に従う。

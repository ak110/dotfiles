# レーン統合タスク：公開統合操作

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 |
| --- | --- |
| AWIの形式を判断する前 | `../skills/wi-standards/references/awi-body.md` |
| manifestの`verification`へ渡す検証の範囲を決める前 | `agent-basics:check-execution/references/verification-scope-selection.md` |
| manifestの`verification`へ渡すローカル検証の上限を決める前 | `agent-basics:check-execution/references/verification-scope.md` |
| 省略された変更の保持とWI継承を検収する時 | `../skills/commit/references/history-rewrite-wi-records.md` |
| 「マージありの統合」でrebaseが競合で停止したとき、またはrebase後の変更範囲の検証が失敗したとき | `agent-toolkit:process-wi`の[references/lane-integration-failure.md](../skills/process-wi/references/lane-integration-failure.md) |
| `マージあり`の統合を開始する時（統合時の完成条件判定を行う時） | `exec-review-recording-integration.md` |
| 「マージありの統合」の検証を実行した後 | `agent-basics:check-execution`の`references/diagnostics.md` |
| `adopt`・`reject`のメモを書く直前 | `../skills/wi-standards/SKILL.md`、`../skills/wi-standards/references/states-and-dependencies-completion.md` |

## 公開統合操作

**4.** 受理済み入力からmanifestと許可集合を準備し、統合区分に応じて手順5または7の公開操作を行う。

単一担当の実行契約において、実行レビューを受理したレーン担当が統合入力を確定し、本節の操作へ進む（初期選定で複数レーンが存在する場合は`統合許可`を受領した後に進む）。
レーン担当は、`exec.parent.md`、`exec-parent-integration.md`に定める統合前検収（完成条件証拠、レビューの収束、対象HEAD、採否の確認）を完了させてから統合操作を実行する。
所有登録、レビュー済みHEAD、完成条件証拠、および最終化の検収条件は同一のものを用いる。

受け取った完成条件証拠を検収し、全計画と今回のWI群、確定した採否や延期の情報を順序付きマニフェストへ保存する。
Gitの所有確認、rebase、内容比較、再検証、fast-forwardマージ、最終化、および選定状態の統合更新は、次の公開操作コマンドで実行する。

```text
atk lane integrate --record <所有登録の絶対パス> --reviewed-head <統合入力OID> --target-worktree <統合先worktreeの絶対パス> --target-branch <統合先branch> --manifest <manifestの絶対パス> --session-id <所有session>
```

`--session-id`には所有登録に記録されている`session_id`（`atk lane create`を実行した所有セッション）を渡し、統合を実行している担当自身のセッションIDに置き換えないようにする。
統合前の稼働判定では、統合先worktreeおよびレーンworktreeをカレントディレクトリ（cwd）として動作中の担当を調べる。
このうち書き込みを行う役割（役割文書で`writes_worktree: true`と宣言されているもの）、役割文書を特定できない起動（自由記述の`delegate`、`shell`、`write`など）、宣言を読み取れない担当を計上する。
所有セッション、統合するレーン担当の委譲元チェーン、ならびに`explore`や`writes_worktree: false`の役割を持つ担当は、指定しなくても稼働担当から除外される。
初回から保持し更新してきた`書込担当一覧`に含まれる合意済みセッションIDを、`--writer-session <session_id>`として必要な数だけ繰り返し指定する。
最初の統合呼び出しの時点から一覧の全要素を指定し、初回の入力で許可対象を漏れなく揃える。
一覧に含まれない実際の書込担当が残っている場合、CLIは該当するすべてのセッションID、ラベル、役割文書、cwd、所属worktreeを表示し、Git変更の前に終了コード2で処理を中断する。
新たに合意を得た担当があれば一覧を更新して同様に指定へ追加し、合意しない担当については終了を待つ。
マージありの統合、マージなしの統合、および失敗後の再開のいずれにおいても、同一の許可集合を使用する。

マニフェストはJSONオブジェクトであり、以下のフィールドを持つ。

- `plans`: 全計画を保持する順序付き配列。
  各項目は`name`（計画ファイル名）と`commands`（`lane-integration-subagent-finalization.md`「計画最終化」で実行するplan-checkや保存用のargv配列）を持つ。
  すでに保存済みで進捗を追記していない計画は`commands: []`とし、`saved`に同名の保存済み計画の絶対パスを指定する。
  実装しなかった計画には`no_merge: true`を付与する。
  計画を作成していない場合は`[]`とする。
- `wis`: 選定された今回のレーンに属する全WIの順序付き配列。
  各項目は`name`（WIファイル名）と`commands`（`lane-integration-subagent-finalization.md`「AWI行の終了」で実行する確定済みの終了用argv配列）を持つ。
  `adopt`する項目は、対応記録の元となる計画または引き継ぎ記録Markdownの絶対パスを`commit_record`へ指定する。
  実装なしでも同じ所在を指定し、commitの無指定を対応記録の空集合と比較する。
  延期する項目、終了しない項目、およびすでに終了済みの項目は`commands: []`とし、その区分と理由を引き継ぎ記録に残す。
  実装しなかった項目には`no_merge: true`を付与する。
- `verification`: 統合前・rebase後の検証argvの順序付き配列。
  レーン担当は、計画の`## 検証`（訂正済み検証表があれば同表）、計画なしは引き継ぎ記録から、各チェックに必要な対象と保存済み成功の被覆を比べてargvを選ぶ。
  静的検査とテストを含む一括コマンドはチェック単位へ分け、既存の公開オプションで被覆済みのチェックを除いた起動形を選べるか確かめる。
  必要な全対象が既存成功と追加実行で覆われることを検収し、対応と選定理由を委譲元とレーン担当が読む既存の検証記録へ残す。
  dirtyな結果は実行時本文の保存物、cleanな結果は取得HEADに対応付け、実効対象、直接影響範囲、ツール・環境の版、設定・lock・外部入力、オプション、終了状態・診断を確認する。
  本文・条件を同定できない範囲、失敗・未実行・対象不足、後続修正やrebaseで結果が変わる範囲は追加実行へ戻す。
  保存済み成功で覆えない全体検証と、統合後に初めて変わる呼出元・組み合わせは残す。
  統合先が基点のままであることだけで環境と被覆まで同じとは判定しない。
  選んだコマンドを文字列配列へ変換し、CLIがレーンworktreeで直接実行する。
  重複コマンドは1回だけ登録し、省略計画と代表コマンドの対応を引き継ぎ記録へ残す。
  同一の成功済み検証はfast-forward前の再実行対象から除く。
  コマンド自身のタイムアウト（pyfltrのcommand-timeoutなど）を使い、必要な外側の上限はmanifestの`verification_timeout`へ指定する。
  CLIは共有検証枠を取得した後に上限を適用し、時間切れ・明示中断では所有する子孫の回収を確認する。
  親コマンドの終了だけで回収済みと判定せず、残存の診断があれば同じ実行記録から処置する。
  秒数は記録された同一検証の所要を基に通常完了を十分上回る有限値とする。
  訂正表の保持・引継ぎは`../skills/write-plan/references/plan-file-standards-verification.md`「検証と公開工程」、結果の検収は`agent-basics:check-execution`「統合実行ツール経由の起動」に従う。
  rebase後は新HEADの変更箇所と関連する利用側を検証する。
  依存版指定・lock更新を含め、ローカルの範囲は`agent-basics:check-execution/references/verification-scope.md`の上限に従う。
  CIが全体を担う場合は全体の試験・静的検査をCIへ委ね、列挙した利用側と関連モジュールまでをmanifestへ渡す。
  CIが無い、または公開先でCIが実行されない場合の全体検証を含め、選んだ範囲と根拠を引き継ぎ記録へ残す。
- `handoff`: 計画を作成していない場合に使用する、既存の引き継ぎ記録の絶対パス。
  計画が存在する場合は省略可能であり、省略時に引き継ぎ記録を追加で入力する必要はない。
- `verification_records`: 任意の保存済み実行記録JSONの絶対パス配列。
  省略時は空配列として扱う。
  `integration.results`の`verification-<番号>.record_path`を渡せる。
- `verification_timeout`: 各検証の処理時間上限（正の有限秒数）。
  省略またはnullはCLI側の上限なしで、コマンド自身の上限を使う。
- `lane_started_at`: 受け取った`レーン開始時刻`（タイムゾーン付きISO 8601形式）。
  plansやwisのargv内で確定値を参照する場合に必須となる。
  CLIは最初に受け付けた値を所有登録に保持し、それ以降に異なる値が渡された場合は処理を拒否する。

plans・wisのargvでは`{{integrated_head_short}}`（統合後HEADの短縮OID）、`{{completed_at}}`（完了時刻）、`{{lane_seconds}}`（`lane_started_at`から完了までの総経過秒）を使える。
本文ファイルを渡す操作は`{{expand-file:<テンプレートの絶対パス>}}`とする。
レーン担当は統合前に自身のmanaged-tempへテンプレートを作り、実在する絶対パスだけをmanifestへ指定する。
CLIは最終化時にプレースホルダーを展開した複製を作り、そのパスへ差し替えて実行する。
複製は準備時に存在しない。
確定値はGit統合成功後・最終化の最初の操作前に一度だけ生成して所有登録へ保存し、シェルを介さず展開する。
再開時も保存値を使い、返却JSONの`finalization_values`へ同じ値を含める。

`verification`には検証自体のargvを保持し、保存済み成功は`verification_records`で渡す。
前述の被覆に基づく実行範囲の選定と、次のCLIの完全一致による自動再利用を区別する。
チェック構成の異なるargvやdirtyな保存物をCLIの一致条件へ混ぜず、レーン担当が証拠を検収して追加実行の範囲を選ぶ。
再利用は、実行前後で同じcleanなGit状態を保持した記録に限る。
CLIはcleanなHEAD、argv、cwd、Pythonと環境の実行条件が一致し、正常な終了0と両出力の保存を確認できる記録を選ぶ。
一致する成功だけを再利用し、`integration.results`の`reused_from`へ元記録を残す。
再開時には同じレーンが既に保存した成功にも、同じ条件を適用する。
HEAD・実行条件が変わった検証、未完了・失敗・必要項目の欠けた記録は再利用せず検証自体を実行する。
再利用した成功と新しい実行結果を区別して検収する。

argvは文字列配列とし、対象ファイル・OID・採否・完成条件証拠は受理済み入力から確定する。
採否とWI別commit対応はレーンが決める。
稼働時間・採否・証拠・進捗追記は本書の検収後にmanifestへ設定し、所有登録の`integration.results`から成功済み操作を確認して再実行から除く。

各検証の結果は所有登録の`integration.results`内の`verification-<番号>`に保存される。
`record_path`から終了ステータスと標準出力・標準エラー出力の保存先を確認して検収する。
同じ記録の`timing`と`timing_path`は外側の取得待ちとコマンド全体の区間を示す。
コマンド内のpyfltrが記録した本体・取り直し待ちは、その実行IDの公開timingから取得して対応付ける。
`slot_waits`がnullの実行と区間内訳を持たない過去の記録は未計測とし、0秒へ補わない。
実行条件が違う場合の診断は変更したキーだけを示し、環境値を記録・表示しない。

### マージありの統合

**5.** レビュー済み成果を保持して統合し、rebase結果を検収して最終化する。
診断は手順6で判定する。

統合許可を待つ間にmanifestと検証表を準備し、許可受領・統合開始・終了を計画の進捗ログへ対応付ける。
統合後に初めて変わる呼出元・入力・組み合わせだけを追加観測し、内容と条件が一致する保存済み成功は保持する。

呼び出し側、生成側、消費側の組み合わせに変化が生じる場合は、その変化の影響を受ける受入シナリオを統合後に自己検収する。
公開されている呼び出し手段と入力値を用いて、期待結果と実際の結果が一致することを確認する。
結果に影響を与えない観測結果については、取得時のバージョンと適用理由を記録したうえで再利用する。

公開統合操作の実行時には、作業ツリーがcleanであること、Git共通ディレクトリとブランチの登録状態、レビュー対象HEADの一致、および所有セッションの正当性を検収する。
「公開統合操作」の稼働判定で対象となる担当プロセスが動作中である場合や、Gitの処理が中断状態にある場合は、作業成果をそのまま保持する。

統合先ブランチのHEADの子孫になっている場合は、rebaseを行わずにfast-forwardマージへ進む。
分岐している場合は、書き換え対象となる全コミットがリモート追跡ブランチへ未到達であることを確認し、旧ベースとHEADを保存したうえでrebaseを実行する。
親コミットのみを操作するrebase、およびその続行や中断の処理では、呼び出しごとに`git -c submodule.recurse=false`を適用し、初期化済みのサブモジュールのHEAD、ブランチ、作業内容を保護する。
すでにpush済みである場合、競合が発生した場合、内容に不一致がある場合、または検証に失敗した場合は、続行できない理由と所有記録を返す。
競合は`lane-integration-failure.md`に従って解消し、まず`--resolved-conflict`なしで同じCLI操作へ戻る。
比較が示した旧commitだけを同指定へ渡し、3-way適用で同じになる文脈行の変化は指定しない。
検査に成功した集合だけが保存され、誤指定は外すか直して再開できる。

rebaseの完了後は、history-compareによる旧commit全件の一意な対応表と、変更範囲の再検証結果を保存し、`phase: awaiting-validation`とともに終了コード1を返す。
部分省略・全件省略された変更の保持とWI継承は、`../skills/commit/references/history-rewrite-wi-records.md`「WI実装commitの対応の継承」に従って検収する。
対応表、後述する警告の判定、WI実装commitの対応更新、および完成条件証拠の再判定の要否を検収したうえで、同一の入力に`--accept-rebased-head <返された完全new_head>`を追加して処理を再開する。
受理するまでの間は、統合先を作業前の状態のまま保つ。
比較処理自体が失敗した場合も、保存された旧新の端点を保持して同一のCLI操作から再開し、完了済みのrebaseは再実行対象から除く。

Git統合前に検証argv・最終化argv・handoffを直しmanifestだけが変わったら、レビュー済みHEADと更新manifestで`--restart-review`する。
現在HEADが保存`new_head`と同じrebase結果では、保存`old_head`または`new_head`を`--reviewed-head`へ渡す。
独立文脈レビューは不要で、継承した対応表と再検証の`awaiting-validation`を検収後、`--restart-review`を外して`--accept-rebased-head`で再開する。
受理後の`verified`からでも検収待ちへ戻る。
rebase後の修正commitでHEADが進んだ場合も、独立文脈レビューを受理した現在HEADと更新manifestで`--restart-review`し、最初の旧系列の対応と追加commit一覧、現在HEADの検証結果を検収してから受理する。
保存`new_head`の子孫でないHEADは拒否される。
直前の進捗・結果は`previous_integration`に保持される。
Git統合済みならレビューHEADと対象集合を保持し、残る最終化だけを再開する。

`--restart-review`では統合先の最新HEADを取り直し、前進していれば未公開のレーン成果をその先端へ再rebaseする。
最初の旧系列から最新の新系列への対応表とWI対応を検収し、変更後HEADの検証結果を受理する。
同じHEADで入力条件だけを訂正した場合も、条件が一致する成功だけを再利用して検収待ちへ戻る。

追加commitを含む再rebaseでは、返却された`additional_rewrites`をJSONへ保存し、各計画または引き継ぎ記録の`plan-progress --rewrite-map`で追加分の対応を先に更新する。
続いて、最初の旧系列だけを含む`rewrite_map`を`plan-rewrite`へ渡す。
旧系列と追加分は異なる履歴上にあるため、単一の`previous-head`を要求する`plan-rewrite`へ一表として混ぜない。

受理前にWI対応を更新し、`plan-commits`の現在の出力からmanifestの`wis`の`--commit`を作り直す。
CLIはfast-forwardの直前に、各adoptのcommitが統合HEADの祖先であり、`commit_record`の対象AWIのcommit集合と一致することを検査する。
実装なしはcommitの無指定を検査し、不一致はWI・指定値・期待集合と次の操作を示して終了2で止まり、Git統合もWI終了も実行しない。

rebaseの対応表によって確定したWIのcommitや、保存失敗の原因を修正した未完了の最終化argvは、更新したマニフェストと`--amend-finalization`を用いて補完する。
CLIはレビューHEAD、統合先、検証内容、引き継ぎ記録、全計画およびWIの集合と順序、ならびに成功済みのargvを保持する。
検収待ちを受理する際は同時に`--accept-rebased-head`を指定し、Git統合がすでに完了している場合はGit操作を再実行しない。

CLIはfast-forward後に`phase: git-integrated`を保存し、plans・wisのargvを順次実行して成功を記録する。
計画保存・採否確定が途中で失敗しても、同じmanifestとレビューHEADで再実行すればGit統合と成功済み操作を省いて最終化を再開できる。
全操作成功後にpick-wi-checkで選定全体を検査して統合状態を更新し、`phase: finalized`と終了0を返す。
追加WIの統合には現在のレビューHEADと累積全計画・WIの新manifestを使い、処理中の入力は変えない。
Git統合後の再開では、所有登録に保持されている成功済み操作と残りの最終化処理に基づき、同一の入力から継続する。
rebase後であっても、完成条件証拠の`レビューHEAD`は実際にレビューを実施した最終バージョンとして保持し、統合入力HEADとは区別して扱う。

rebaseを実行した場合は、`history-compare`の対応表を`agent-toolkit:commit`の`references/commit-wi-records.md`「WI実装commitの対応」に定める履歴変更手順の`--rewrite-map`へそのまま渡す。
追加commitを伴う再rebaseでは、前述の追加分の更新後に旧系列の対応表を渡す。
この際、対応表の旧系列のHEADと統合対象のすべての計画または引き継ぎ記録も、同一の呼び出しで一度に渡す。
rebaseによってOIDのみが変化した計画の完成条件証拠については、再判定する必要はない（`exec-review-parent-launch.md`「起動方法別のレビュー基準」を参照）。
統合先との組み合わせによって内容が変化し、結果に影響を与える差分が生じた条件に限り、新しいHEADで取得し直した検証結果を添えて同一の実行レビュー担当に再判定を依頼する。

### 検証結果の警告の判定

**6.** 検証の警告と所要を判定し、手順5の受理・再開判断へ反映する。

検証結果における診断メッセージや警告、および個別・全体の所要時間は、`agent-basics:check-execution/references/diagnostics-results.md`「読む出力」の共通基準に従って判定する。
統合時の比較基準版には統合前のHEADを使用する。
統合処理に特有の扱いは以下のとおりである。

- 比較のためにコマンドを再実行する場合は、統合先worktreeにおいて該当するコマンドのみを読み取り専用で実行し、統合先で追跡しているファイルやブランチを変更しないようにする。
- 新たに発生した警告であるという理由だけで統合やAWIの採否確定を停止させない。
  実行工程で残す判断をした警告については、その意味、影響、帰属、比較対象のバージョン、および残す理由が統合後の状態でも妥当であることを確認し、診断本文と結果を記録する。
  統合の妨げとなる警告は修正し、比較が不可能な場合や意味・帰属が特定できない場合は統合を中断して、理由と不足している条件を「続行できない理由」として報告する。

## 本文参照の取得条件

| 時点または条件 | 全文読む資料 |
| --- | --- |
| 検証結果の診断と警告の判定：読む出力の基準を適用する前 | `agent-basics:check-execution/references/diagnostics-results.md` |
| 編集時の注意点:の基準を適用する前 | `../skills/commit/SKILL.md` |
| レーン統合タスク：計画最終化の基準を適用する前 | `lane-integration-subagent-finalization.md` |

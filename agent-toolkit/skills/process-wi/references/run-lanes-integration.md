# レーンの実行：統合とAWI終了

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 目標起点の判断を行う前 | `lanes-goals.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/references/lanes-goals.md` |
| レーンの専用worktree・所有登録を作成・再利用・回収する前、`レーン開始時刻`を記録する前、後段レーンの先行調査を始める前 | `lane-resources.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/references/lane-resources.md` |
| 本文で基準を使う資料の選択・取得時 | `../../../share/exec.parent.md` | 判断 | `read:agent-toolkit:*/share/exec.parent.md` |
| 追加WIと即時修正を区別する前 | `immediate-fix.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/references/immediate-fix.md` |
| 参照基準の適用前 | `../../../share/exec.parent.md`、`../../../share/exec-parent-integration.md` | 判断 | `read:agent-toolkit:*/share/exec.parent.md`、`read:agent-toolkit:*/share/exec-parent-integration.md` |
| 基準資料を選択・取得する時 | `finish-session-observation.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/references/finish-session-observation.md` |
| 基準資料を選択・取得する時 | `../../../share/exec-review-recording-integration.md` | 判断 | `read:agent-toolkit:*/share/exec-review-recording-integration.md` |
| 基準資料を選択・取得する時 | `lane-planning-resume.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/references/lane-planning-resume.md` |
| 基準資料を選択・取得する時 | `finish-session.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/references/finish-session.md` |

## 統合とAWI終了

**18.** 統合前の確認作業は、`../../../share/exec-review-recording-integration.md`「統合時の完成条件判定」に従う。
レーン担当へ委ねる統合指示、およびメインエージェント自身がCLIを実行する場合の検収は、`../../../share/exec.parent.md`、`../../../share/exec-parent-integration.md`に準拠する。

**19.** Git統合、全計画の保存、WIの採否確定、選定情報の統合状態更新は、同一の所有登録をもとに`atk lane integrate`が一括して実行する。
呼び出し引数とmanifestの作成手順は、レーン担当の`lane-integration.subagent.md`「公開統合操作」に従う。
メインエージェント自身が現行CLIを呼び出す場合も、独立した文脈でのレビューと完成条件の判定を通過した同一の入力情報を使用する。
Git統合と統合状態の更新は、この公開操作に集約して実施する。

**20.** 終了コード0、`phase: finalized`、統合結果のOID、計画ファイルの保存、ならびに終了対象AWIの採否確定を確認する。
`終了しない`項目と延期対象は採否を確定しない。
`git-integrated`は最終化処理の途中の状態であり、統合完了とはみなさない。
処理が失敗した場合は、同一のmanifestとレビュー対象HEADを用いて未完了の工程から再開する。
rebase後のコミット対応表と検証記録は所有登録と同一の状態ディレクトリへ保存され、一時領域から失われた過去の状態は同一操作によって再生成されて検収待ちの状態へ復帰する。
CLIは今回のWI集合と所有権を検収し、すべての最終化操作が正常に完了した後に選定原本を不可分（アトミック）に「統合済み」へと更新する。
統合前の稼働確認では、統合先worktreeとレーンのworktreeをカレントディレクトリ（cwd）として動作中の担当エージェントを確認する。
書き込み役割を持つもの（役割文書に`作業ツリーへの書込: あり`と明記されているもの）、役割文書を特定できないもの（自由入力による`delegate`、`shell`、`write`など）、宣言内容を読み取れないものをカウントする。

 所有セッション、合意済みの`書込担当一覧`、統合を呼び出す担当の委譲元チェーンを共通の認可セットとする。
 `explore`や`作業ツリーへの書込: なし`と明記されている担当エージェントは書き込み判定から除外し、認可一覧に含まれない実際の書き込み担当が存在する場合にのみGitの変更処理前に拒否する。
 合意済みの担当エージェントは`--writer-session`で指定する（`lane-integration.subagent.md`「公開統合操作」）。
リソース回収時は、すべての担当エージェントと外部プロセスが終了していることを確認する。
リソース、完了済みレーン、所属WIは、追加タスクの発生に備えて保持する。

**21.** リソースの回収は、セッション終了時に`atk lane delete`によって実行する。
登録全体を事前に確認し、すべての工程および外部プロセスが終了していること、選定情報が統合済みであること、存在するworktreeがクリーンであること、Git共通ディレクトリと専用branchの登録が一致していること、および統合先への到達が可能であることを削除直前にも確認する。
`finalized`となったレーンについては、branchのHEADが統合時に記録された入力HEADと一致している場合、統合先でコミット履歴の書き換えが行われた後であっても統合済みとして回収する。
実体が存在しない登録については、これらの確認判定を省略する。
確認完了後は、実在するworktreeに対してのみ`--commands-file`で指定された`delete`コマンドを実行し、worktree、統合済み判定を再確認したbranchの`-D`削除、旧配置の登録が保持する個別managed-tempの順序で回収を進める。
未統合、dirty、稼働中、状態不明、または処理途中で失敗した対象については、その理由と残存する操作内容を回収対象の外部へ保存し、再実行時には残りの処理から再開する。
セッションのmanaged-tempや証拠ファイルは専用worktree内には置かず、回収対象から除外する。
通常終了時の`atk agents-exit-session`、および`process-loop`が子セッション終了後に実行する処理は、同一の回収操作を呼び出す。
ターンごとの`SessionEnd`イベントでは回収しない。

 自動終了のいずれの経路でも同一の適格判定基準を用い、終了済みセッションからの解放応答を待機せず、未確認の事項があれば既存の保持理由と結果の保存先を返す。
 対話的な`lane delete`、固定作成先での`lane create`、`--reuse-record`、ならびに`lane recover`の各操作は、所有者側の解放応答と実行中プロセスの終了を指定の期限まで待機する。
 解放要求の完了後に別の担当や外部プロセスによる使用が検知された場合は、一度の照会によって保持理由を更新し、同一の公開操作による再判定へと回す。
 終了済みの委譲セッションが保持しているリソースについては、回収処理が所有者側の`agents_server`に対して解放を要求し、未回収の結果を保持した状態で解放処理してから、使用中かどうかの判定をやり直す。
 実行中のセッションや解放できないリソースは破棄せずに保持し、次項の手順に従って再回収する。

到達確認を行った上で`-D`オプションによりbranchを削除する理由は、通常の`-d`オプションがupstreamを基準に判定するため、push前のローカル統合を未統合と判定して削除を拒否してしまうためである。
監査記録は`docs/development/audit-records.md`「agent-toolkit/skills/process-wi/references/run-lanes.md「統合とAWI終了」：所有資源の回収：2026年9月3日」に記載されている。

### 未回収レーンの再回収

1. 回収結果に未解放として示された委譲セッションについて、終了状態であること、および`atk agents wait`によって結果本文が回収済みであることを確認する。
   今後再開する予定がない場合は、公開MCPである`agents_server`の`stop`ツールを呼び出し、`session_id`として該当のIDを指定する。
   呼び出し形式は`stop(session_id="<回収結果にある委譲sessionのID>")`とする。
   公開ツールである`stop`は終了結果も同時に破棄し、結果保持を指定するための引数は存在しない。
   これに対し、回収処理が所有者側へ要求する自動解放処理では、未回収の結果が保持される。
   現在実行中のセッションについては終了を待機し、その結果を回収する。

2. 停止処理が成功してリソース保持の理由が解消された後は、対象リポジトリを作業ディレクトリ（cwd）として`atk lane delete --session-id <レーン資源を作成した所有sessionのID>`を再実行する。
   `--session-id`には、元の回収操作と同一の「レーンリソースを作成した所有セッション」のIDを指定する。
   このIDは`atk lane delete --list`の所有登録情報から確認でき、`stop`に渡す委譲セッションのIDとは役割が異なる。
   停止処理に失敗した場合や、外部プロセスによる使用が継続している場合は、メインが共通判断で用途と現在の担当を調べる。
   不要と確定したプロセスは個別に停止して同じdelete操作へ戻し、使用中の対象は引き継ぎや終了待ちへ進む。
   保持する場合は、リソースと回収結果に加え、保護対象または残るユーザー判断と再開条件を報告する。

## 中断レーンの再開

1. `processing`状態のまま残存している項目については、計画が存在するレーンでは選定結果の`再開位置`に記載された計画ファイルを使用する。
   複数の計画が存在する場合は、引き継ぎ記録に蓄積されたすべての計画、対象AWI集合、進捗状況、および保存状態を突き合わせた上で引き渡す。
   計画のないレーンでは、引き継ぎ記録のAWI集合、レビュー指摘管理表、ならびに`git merge-base <専用branch> <ベースbranch>`および`git log <その結果>..<専用branch>`によるコミット履歴を確認して再開すべき工程を決定し、`再開位置: 計画なし`として引き渡す。
   計画ファイルまたは引き継ぎ記録の実在、AWI集合の一致、および専用worktreeの元の所有主体が終了済みであることを事前に確認する。
   反映後の観測のみが残っている項目については、`finish-session-observation.md`「延期adoptと反映後の観測」によりAWI本文へ追記された再開記録に基づき、選定結果の観測専用の再開位置をそのまま引き渡す。
   レーン担当は`lane-planning-resume.md`「再開」に従って観測作業のみを実施する。

2. 残存している専用worktreeおよびbranchを安全に再利用できる場合は、同一の絶対パス、branch、最新HEAD、および再開位置を新規のレーン担当へ渡す。
   安全に再利用できない場合は、既存の作業成果を損なわないよう最新HEADから新規の専用worktreeを作成し、移行内容を`atk run-script plan-progress --`を用いて進捗ログ（計画なしの場合は引き継ぎ記録）へ記録する。
   コミットされていない差分については所有権と実態を確認し、権限の範囲内で成果物を損なわずに復元・移送する作業は自ら行う。
   他者やユーザーの差分の破棄、あるいは所有権を確定できない成果物を消失させるおそれのある処置については、「確認要否の判定」に従って事前に認可を求める。

## レーン完了

**22.** すべてのレーンについて、統合結果の検収、AWIの終了または保留への状態遷移、および計画ファイルの保存を完了させ、リソースを終了時の回収処理へ引き渡す。
追加された処理対象WIは、元の選定結果へ反映された分のみを含める。
公開工程へは、`references/finish-session.md`冒頭に定められた開始条件が成立した後に進む。

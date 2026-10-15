# 継続処理の起動・隔離・制御の設計記録

本書は[設計記録の索引](design.md)から主題別に分割した記録であり、機構の目的、構造の理由、知識境界と却下した代替案を保持する。
実行時に適用する規範は、各節が参照する現行のルールファイルとスキルが定める。

## process-loopのオーケストレーター選択

`atk wi process-loop`の常駐セッションで使用するengine・model・effortは、工程別モデル設定の候補列で決定する。
候補列はorchestrate用途のengine順（`orchestrate_engine`）にengine別の上位段位のモデル（`claude_high_tier_model`・`codex_high_tier_model`）を並べたものである。
各値は`agent-toolkit/agent_toolkit/_atk/config.py`の`resolve_mutable_setting`が、環境変数`AGENT_TOOLKIT_CONFIG_<キー名の大文字>`、保存値、未設定時の値の順に取得する。
未設定時の候補列は、`codex-balanced`プリセットのengine順に段位モデルの未設定時の値を並べた`claude:opus[1m]/medium,codex:sol/medium`である。

`agent-toolkit/agent_toolkit/_atk/wi/process_loop.py`の`cmd_process_loop`は開始時に`process_loop_session.py`の`resolve_orchestrator_specs()`で設定値を検証する。
さらに子セッションを起動する前ごとに同関数で候補列を読み直し、`select_available_orchestrator`が先頭の候補から可用性を判定して最初に利用できる候補で起動する。
このため常駐中に`atk config set`で保存した変更は、次の子セッションの起動前に反映される。
空でない環境変数がある間は、保存値を変えても環境変数の値が実効値になる。
候補列を子セッションの起動前ごとに読み直すため、可用性とCodex系列名の解決は起動時点の利用枠とモデル一覧で判定され、設定の変更も常駐プロセスを再起動せずに次の子セッションへ届く。

CLI引数の`--orchestrator`・`--model`を設定と併存させる案は、設定との優先関係を複雑化するため
ユーザー合意で廃止した。

Codexの`astra`・`sol`・`terra`・`luna`は設定へ系列名として保存する。完全IDへの解決は`resolve_orchestrator_specs()`内の`resolve_model_candidates("orchestrate")`が子セッションの起動前ごとに行い、App Serverの`model/list`を最終ページまで取得して表示対象の同系列の最新版へ解決する。委譲起動は保持中のApp Server接続を使い、設定CLIは短命の接続を所有する。選んだ完全IDが指定effortを受理しない場合は理由を返し、別系列または旧版へ暗黙に置換しない。明示された完全IDは固定し、engine側の可用性判定が返す診断を候補とともに示す。Claude候補の可用性判定は構造化出力（`stream-json`）で起動し、Weekly limitか5時間の利用上限による拒否を出力の利用枠情報から判定する。この拒否では次の候補へ進まず、解除予定時刻（不明なら300秒後）まで待って同じ候補を判定し直し、待機の種類と解除予定時刻を端末とprocess-loopのログ（`usage_limit_wait`）へ出力する（ユーザー指示）。本作業のセッションが利用上限で終わった場合も、次の反復の可用性判定が同じ待機へ入る。モデルを試行起動して系列の最新版を推測する案は、実行費用を生み、利用可能なモデル一覧との対応も保証できないため採用しない。複数の起動処理が同じ判断を持たないよう、一覧取得と系列解決の知識境界を`agent-toolkit/agent_toolkit/_common/codex_models.py`に置く。

工程別モデル設定のCodexの中位（`codex_medium_tier_model`）で値を保存していない場合に使うモデルと、以前のプリセットの「軽量」「探索上位」は`terra/medium`を選び、「上位」と「探索軽量」は各用途の指定を維持する。`luna/xhigh`を前二者へ残す案は、ユーザーが速度と精度の釣り合いを見直したため採用しない。これらのモデル名は運用中に変わる設定値であり、テストは用途別engine順と段位モデルからの候補の合成、保存と実行時解決の接続を確認する。

Codexの可用性判定で利用上限を観測した場合は、次の候補を先に試す。
全候補が失敗し、そのうちに利用上限の失敗がある場合だけ最も早い解除予定まで待ち、先頭から判定し直す。
解除予定が不明なら300秒後に再確認し、待機の回数と総時間を打ち切らない。
上限判定は共通層がロールアウト記録から行い、選択と待機はorchestratorが持つ。

## process-loopのモジュール構成

`atk wi process-loop`の工程は`agent-toolkit/agent_toolkit/_atk/wi/`配下の責務ごとのモジュールが持つ。作業ツリーの準備と上流との同期は`process_loop_worktree.py`、自己更新の確認と再起動は`process_loop_update.py`、miseの更新は`process_loop_mise.py`が持つ。変更の監視とprivate-notesの同期は`process_loop_watch.py`、プロンプトとオーケストレーターの選択とセッションの起動は`process_loop_session.py`、アラートの確認は`process_loop_alerts.py`が持つ。中断要求と追加指示の状態ファイルは`process_loop_control.py`、子プロセスの環境と実行ファイルの解決は`process_loop_env.py`が持つ。`process_loop.py`の`cmd_process_loop`はこれらを順に呼ぶ制御だけを持つ。以前は1つのファイルが全ての責務を持ち、工程を加える計画ごとに同じファイルへ追記していた。モジュール間の呼び出しは定義元のモジュールの属性として行い、テストは定義元を差し替える。テストも同じ単位で`process_loop_<責務>_test.py`へ分ける。

CI失敗の収集・キー重複除外・本文生成・AWI保存は`alerts.py`が持ち、単発の`atk wi check-alerts`と常駐側が同じ本体を呼ぶ。単発は対象作業ツリーの解決と取得・投入結果を報告して終了し、process-wiの1回の実行やDependabot監査を起動しない。`process_loop_alerts.py`は監視間隔とDependabot監査の起動要否を持ち、収集失敗では従来どおり待機を続ける。単発用に収集処理を複製する案は、本文・キー・取得範囲の一致を別々に保守するため採らない。呼び出す時機は各環境が選び、ガイド「推奨ワークフロー」はその境界を示す。

CI失敗の本文には実行日時、対象commitのSHA、現在のブランチHEADとその比較結果を含める。
HEADを取得できない場合は理由を示す。
古い失敗だけが残る場合は、HEADで実行が作成されない理由と、失敗原因が現行HEADにも残るかを調査する。
原因が残らない場合は確認結果の記録で完了でき、追加変更や後続実行を求めない完成条件を渡す。
GitHub、GitLabのブランチとScheduleの収集が同じ実行事実を渡す。

`_git/forge_cli.py`は`wait_ci.py`と`alerts.py`が使う非対話のログ取得案内を所有する。
案内するコマンドは対象リポジトリを明示し、Git作業ツリー外からも実行できるargvを引用して示す。
GitLabでjobが未確定の場合は、job一覧の取得と対象jobのtrace取得を順に案内する。
収集側と待機側で同じ案内を使い、CLIの対話画面へ依存する手順の分散を防ぐ。

GitLabのリポジトリ識別子とAPI接続先は別に扱う。
SSH接続先とAPI接続先のDNS名やポートが異なる場合、baseline作成・待機・再開へ同じAPI側のrepository指定を渡す。
`_git/remote.py`が接続先を解析し、`_git/forge_cli.py`がglabの子環境へ渡す接続先指定を共通に所有する。
呼び出しごとの子環境は`_common/json_command.py`が受け取り、親環境を保持する。
この接続先を一覧、bridges、downstreamとjobの照会に使い、キュー識別子の正規化とbaselineの保存形式は維持する。

## 開始前の活動猶予とUWI回答待ち

開始可否は常駐側が持ち、`process_loop_watch.py`の変更・期限・中断待機へ接続する。
別セッションの活動猶予と未回答UWIの抑止は同時に評価し、待ち時間を順に足さない。
活動猶予は処理可能WIの検出時刻、新規到着の延長は検出時刻、UWIの期限は保存済みファイルのmtimeを使う。
更新の意味が異なるため、全ての時刻を反復の開始時刻へ置き換える案は採らない。
活動がなくなるか10分の期限が来れば活動条件を解除し、新規AWI・UWIの到着ごとに10分へ延長する。
編集・状態移動は同じWI名として扱い、新規到着へ数えない。
UWIは対象集合のinbox・processing・holdの未回答を判定し、最終更新には回答後のadoptedも含める。
holdとrun-skillを抑止へ含めることと処理候補へ含めることは分ける。
全回答か最終更新から600秒でUWI条件を解除し、開始直前に処理可能集合を再評価する。

活動の生成は既存hook、取得と親子解決は共通層、開始判定は常駐側に置く。
PIDと起動時刻、実際のターン終了、子の稼働、登録簿を合わせ、当該ループの子孫だけを除く。
別cwdや別ループも調べ、終了済みと子なし応答待ちを除外する。
未登録・読取不能は期限付きの活動候補として扱う。
最新状態を更新する有限の記録を使い、全履歴の毎回解析と新しい監視サービスは採らない。
端末と既存ログに理由・期限・延長・開始を残し、子セッションへ待機判断を移さない。

## 登録集合の一括取得とall-repos

`process_loop_repositories`はローカルworktreeの絶対パスを持つJSON配列で、`atk wi process-loop all-repos`だけが登録集合を対象にする。
通常のcwd単一対象起動と、登録外へのWI投入を保つことで、投入可能な対象とこのホストで処理する対象を分ける。
空登録は待機し、設定不正や解決不能では対象と修正操作を示して開始を止める。
同じoriginは先頭へまとめ、実worktreeとGit共通ディレクトリを対応付ける。
登録変更は次の取得へ反映する。

`listing.configured_repo_snapshot`がキューを一括取得し、listingとreadinessへ共通入力を渡す。
`atk wi list --configured-repos`も同じ登録集合を使う。
長い対象引数と対象数分のキュー取得を繰り返す案は、少数WIの対象が多数ある運用で費用を増やすため採らない。
候補と監査の開始条件が無い対象は担当を起動しない。
開始前の活動・UWI条件は登録集合全体へ作用し、開始前更新・自己再起動は全体で1回だけ行う。

`process_loop_all_repos.py`は取得済み候補・readiness・対象別上限・所有記録を各対象cwdの通常delegateへ渡す。
その担当がプロジェクト規範とprepare、選定・監査・実装・Git・CI・公開を担い、ホームのオーケストレーターは中継を担う。
ホームの規範だけで全対象を直接編集する案は、対象規範とworktree設定が届かないため採らない。
通常起動と同じoriginの所有を共有し、短い排他の後は担当sessionと登録記録で処理を保持する。
回答待ちや長い処理の間にロックを保持する案は、別担当の進行を妨げるため採らない。
開始後の失敗・所有待ち・監査・CI・再開は対象別に保持し、別所有者の終端後は候補を取り直す。
異常終了の未統合成果は既存の再開へ渡し、all-repos担当と通常会話のresumeを区別する。
制御はOSアカウント単位を保ち、instructは単一起動では次の処理会話へ1回だけ渡す。
all-reposでは次の起動集合で1回受領した本文を各担当へ中継し、各担当は自分の対象に関する部分を適用する。
実行契約は[all-repos-orchestration.md](../../agent-toolkit/skills/process-wi/references/all-repos-orchestration.md)が持つ。

## process-loopのworktree隔離

`atk wi process-loop`は影響範囲の大きい主作業ツリーを直接編集せずにセッションを起動する。
`--worktree[=NAME]`を指定すると任意の対象リポジトリでこの隔離を有効にする。
dotfilesリポジトリでは、オプションを指定しない場合も従来どおり隔離を有効にする。

worktreeは対象リポジトリ配下の`.claude/worktrees/<NAME>`へ配置し、専用ブランチ`worktree-<NAME>`を割り当てる。
`NAME`を省略した場合は`process-loop`を使用する。
worktreeの作成前と再利用前に、`.claude/worktrees/`がGitの無視対象であることを確認する。
無視されていない場合は、対象リポジトリの`info/exclude`へ`/.claude/worktrees/`の完全一致行を独立して追加する。
追加後の`git check-ignore`が成功した場合だけ、worktreeの作成または再利用へ進む。
除外判定の失敗、設定の更新失敗、再判定の失敗では、worktreeとセッションを起動せず待機へ戻る。

既存worktreeは、Gitの照会がすべて非空の成功値を返すことを確認してから再利用する。
`--git-common-dir`を対象リポジトリと一致させ、`--show-toplevel`をworktreeの配置先と一致させる。
現在のブランチが`worktree-<NAME>`であり、worktreeがcleanであることも再利用の条件とする。
条件のいずれかを確認できない場合はfetchとrebaseを実行せず、セッションを起動しない。
条件を満たしたworktreeは、現在のブランチの追跡先を優先し、解決できない場合だけ`origin/HEAD`へ後退して得た上流ブランチへfetchとrebaseで追随させる。
解決した上流ブランチはfetch・rebaseの内部だけで使用し、対象リポジトリごとに異なる公開先をセッションのプロンプトへ推測注入しない。公開操作と公開先の判断は、その運用を所有する主体へ委ねる。

並行worktreeの退避は`atk worktree-stash save --label <退避ラベル>`へ集約する。ヘルパーはGit共通ディレクトリ直下の固定`agent-toolkit-stash.lock`を`agent-toolkit/agent_toolkit/_common/file_lock.py`で排他する。ロック中にstashを生成し、`refs/worktree/<退避ラベル>`とworktree・label別の共有保護refを記録してから生成分だけのstashをdropする。固有refだけでは別worktreeのGCから到達できないため、共有する`refs/atk/worktree-stash/`配下へ同じOIDを保護する。既存の`refs/stash`先頭OIDは維持し、途中失敗時は復旧識別子と保護または共有stashを保持する。固定ロックファイルを削除しないのは、次回も同じinodeを排他対象として再利用するためである。未追跡ファイルを含む退避を実現できない`git stash create`方式は採用しない。

正常な旧固有refは`atk worktree-stash protect <ref>`で識別子を変えず保護できる。`update-dotfiles`はpull前に現在worktreeの旧`update-dotfiles-*`退避を全て保護し、欠損や保護失敗ではpullへ進まない。今回退避は`stash apply --index`の成功後だけ`drop <ref>`で回収する。pull失敗後の復元成功も回収し、復元競合とrebase競合では保持する。回収失敗は復元失敗と分け、復元済み内容をresetせず再試行を案内する。dropは固定ロック内で読取OIDを条件として固有ref、共有保護refの順に削除し、片側だけ残っても同じ識別子で再試行できる。他worktreeの同label退避と既存共有stashは回収しない。

Claude Codeの`--worktree`へ置き換える案は、worktree隔離ガードがシェル構文を拒否するため採用しない。
`atk`側でworktreeを準備し、セッションのcwdを準備済みworktreeへ設定する。
対象にWorktreeCreate/Removeがあれば共通のhook呼出しを使い、対象設定・cwd・イベント入力・環境を渡す。
返却先とGit登録・branch・共通ディレクトリ・資源所有を一致させ、設定済みhookの失敗では途中資源と診断を保持する。
通常Git作成への暗黙の切替は、対象が指定した準備・撤去の対応を失うため採らない。
hookが無い対象は固定配置と対象固有のprepareを使い、hook指定先も反復ごとに別名へ回避しない。
all-reposでは対象cwdの担当が準備し、ホーム側へ対象固有の手順を複製しない。

## process-loopの会話IDによる識別

process-loopがClaude会話を新規に起動するときは`--session-id`で会話IDを指定し、同じIDを子環境へ渡す。ID指定の再開では指定値を渡す。hookは環境印とhook入力の`session_id`の一致から対象会話を決める。入れ子の`claude`は環境印を継承する一方で会話IDが異なるため、常駐本体の終了保証と空転ガードの対象にならない。IDを指定しない再開ではprocess-loopが対象IDを渡せないため、環境印による従来の判定を保つ。親プロセスの探索はプロセス階層とOSへの依存を増やし、hook入力に既にある会話IDより間接的な判定になるため採用しない。

## process-loopの中断要求

`atk wi process-loop abort`は中断要求をOSアカウントの状態ディレクトリにある`process-wi-abort`の存在で表す。
process-loopは実行中のセッションが終了した直後に要求を確認し、次の反復へ進まず正常終了する。
終了時は端末ベルを3回鳴らし、各鳴動を端末が区別できるよう短い間隔を置く。
中断要求は終了時に消費し、次回のprocess-loopを通常状態で開始する。

状態ファイルは`process-wi.log`の親ディレクトリへ配置する。
これにより、process-loopと操作コマンドは`XDG_STATE_HOME`を含む同じOSアカウントの状態の解決規則を共有し、対象リポジトリやprivate-notesの初期化に依存しない。
OSシグナルや稼働中プロセスへの直接通知は、process-loopが動いていない時点で要求を設定できず、解除と状態参照に使う永続状態も提供しないため採用しない。

## Codexのprocess-loopセッションの終了

ready項目がなくなると、`agent-toolkit:completion-report`が選定工程で完了した振り返りの結果を含む完了報告を完了し、続いて`atk agents-exit-session`が`/goal`で登録した目的とセッションを終了する。
起動時の終了能力probeと、停止要求の直前に終了対象を新規識別する扱いは`design-workflow-boundaries.md`「セッションの終了」にある。
Linuxでremote-controlを使わない直接CLIを終了対象として確認できた場合は、Codexが自律終了して親の監視ループへ戻る。
終了対象を確認できない環境では対話UIに終了案内を表示し、ユーザーが`/exit`を入力すると親の監視ループへ戻る。

親は自身が起動したCodexの子のcwd・開始時刻からロールアウト記録を対応付ける。
上限による失敗の後に新turnが無い場合だけ、その所有する子へ終了を要求し、正常終了として次の反復へ進む。
新turnがあれば子を保持する。
次の反復は可用性判定で解除を待ち、新しい会話でprocess-wiを起動する。
親の`process_loop_session.py`は子の終了後に`lane.cleanup_session`で回収を試み、未統合・dirty・稼働中などのレーンは所有登録と成果を保持する。
レーンのbranch・worktree・選定原本・旧所有sessionは、`lane.py`が状態ディレクトリの`lanes/<旧所有session>/lane-*.json`へ保存する所有登録が持つ。
状態ディレクトリの絶対パスは`atk config get state_dir`で取得する。
WIの本文と`processing`の状態はprivate-notesのキュー、計画と進捗ログは`~/.claude/plans`が保持する。
新しい会話のprocess-wiは[選定手順](../../agent-toolkit/skills/process-wi/references/selection-procedure.md)に従い、`atk lane delete --list`で残存登録と回収結果を読み、WIに対応する計画を`atk plans list`から解決する。
再開する工程は計画の進捗ログから決め、[中断レーンの再開](../../agent-toolkit/skills/process-wi/references/run-lanes.md#中断レーンの再開)に従って新しいレーン担当へ計画と既存worktree・branch・現在HEADを渡す。
資源を再利用するときは旧所有主体の終端を確認し、`atk lane create`の`--reuse-record <record_path>`へ所有登録のパスを渡して新しい所有sessionへ移す。
この再開は保存した状態からの復元であり、親は中断した会話履歴を新しい会話へ複製しない。
対応する計画や所有を一意に確定できない場合は、既存の再開手順が続行できない理由を返す。
`codex resume`で同じ対話会話を起動する案は、`usage_limited`のgoalの再開確認で無人の子が停止するため採らない。
共有daemonへの`codex queue`を使う案も、effortを指定する`-c`付きの起動がdaemonの対象外であるため採らない。
ロールアウトの判定は共通層、子の終了は`process_loop_session.py`が所有する。
最上位TUIの自動再開とは適用範囲を分ける。

## process-loopの次の起動への追加指示

`atk wi process-loop instruct <本文>`はprocess-loopの次の起動へ渡す本文を、
中断要求と同じOSアカウントの状態ディレクトリの`process-wi-instructions`へ追記する。
`instruct-cancel`は保持中の全件を破棄し、`status`は中断要求の有無とあわせて保持中の件数と本文を表示する。
完全一致する本文の再投入は誤操作とみなして追記せず、保持中の合計が2,000文字を超える投入は拒否する。

process-loopはセッションを実際に起動する直前に状態ファイルを読み取って削除し、
単一起動では得た本文を`AGENT_TOOLKIT_PROCESS_LOOP_INSTRUCTION`として次の処理会話の環境へ載せる。
all-reposでは同じ起動集合の各担当へのプロンプトへ本文を加え、各担当は自分の対象に関する部分を適用する。
AWIが0件で変更検知を待つ反復は起動へ到達しないため、本文は保持したまま次の起動へ残る。
`rules_context`のSessionStartは、`AGENT_TOOLKIT_PROCESS_LOOP_INSTRUCTION`に値がある場合に前置きを添えて`additionalContext`へ加え、
委譲先のセッションへは加えない。この本文がユーザーの入力であり、人間由来の明示的な指示であると前置きに明記する。
本文は`forwarded-user-input`要素で囲み、`agent-toolkit/rules/01-agent.md`「方針が衝突する場合の優先順位」がこの要素の内側だけを機械が生成した本文の中のユーザー発話の証拠として扱う。

本文の上限を2,000文字とするのは、SessionStartの`additionalContext`がClaude Codeで10,000文字に切り詰められ、
超過すると同じ出力に含まれる規範条文が欠落するためである。
`/goal`の目的文へ連結する案は、目的文がターンを終えるたびの目標評価の入力となるため採用しない。
起動後のセッションが自ら状態ファイルを読む案は、全セッションへ読取の1工程を課し、
かつセッションが手順を実行することに依存するため採用しない。

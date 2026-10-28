# 継続処理の起動・隔離・制御の設計記録

本書は[設計記録の索引](design.md)から主題別に分割した記録であり、機構の目的、構造の理由、知識境界と却下した代替案を保持する。
実行時に適用する規範は、各節が参照する現行のルールファイルとスキルが定める。

## process-loopのオーケストレーター選択

`atk wi process-loop`の常駐セッションで使用するengine・model・effortは、工程別モデル設定の候補列で決定する。
候補列は工程別の`orchestrate_model`が直接持つ。
各値は`agent-toolkit/agent_toolkit/_atk/config.py`の`resolve_mutable_setting`が、環境変数`AGENT_TOOLKIT_CONFIG_<キー名の大文字>`、保存値、未設定時の値の順に取得する。
未設定時の候補列は、`codex-balanced`プリセットと同じ`claude:opus[1m]/medium,codex:sol/medium`である。

`agent-toolkit/agent_toolkit/_atk/wi/process_loop.py`の`cmd_process_loop`は開始時に`process_loop_session.py`の`resolve_orchestrator_specs()`で設定値を検証する。
さらに子セッションを起動する前ごとに同関数で候補列を読み直し、`select_available_orchestrator`が先頭の候補から可用性を判定して最初に利用できる候補で起動する。
このため常駐中に`atk config set`で保存した変更は、次の子セッションの起動前に反映される。
空でない環境変数がある間は、保存値を変えても環境変数の値が実効値になる。
候補列を子セッションの起動前ごとに読み直すため、可用性とCodex系列名の解決は起動時点の利用枠とモデル一覧で判定され、設定の変更も常駐プロセスを再起動せずに次の子セッションへ届く。

CLI引数の`--orchestrator`・`--model`を設定と併存させる案は、設定との優先関係を複雑化するため
ユーザー合意で廃止した。

Codexの`astra`・`sol`・`terra`・`luna`は設定へ系列名として保存する。完全IDへの解決は`resolve_orchestrator_specs()`内の`resolve_model_candidates("orchestrate")`が子セッションの起動前ごとに行い、App Serverの`model/list`を最終ページまで取得して表示対象の同系列の最新版へ解決する。委譲起動は保持中のApp Server接続を使い、設定CLIは短命の接続を所有する。選んだ完全IDが指定effortを受理しない場合は理由を返し、別系列または旧版へ暗黙に置換しない。明示された完全IDは固定し、engine側の可用性判定が返す診断を候補とともに示す。Claude候補の可用性判定は構造化出力（`stream-json`）で起動し、Weekly limitか5時間の利用上限による拒否を出力の利用枠情報から判定する。この拒否では次の候補へ進まず、解除予定時刻（不明なら300秒後）まで待って同じ候補を判定し直し、待機の種類と解除予定時刻を端末とprocess-loopのログ（`usage_limit_wait`）へ出力する（ユーザー指示）。本作業のセッションが利用上限で終わった場合も、次の反復の可用性判定が同じ待機へ入る。モデルを試行起動して系列の最新版を推測する案は、実行費用を生み、利用可能なモデル一覧との対応も保証できないため採用しない。複数の起動処理が同じ判断を持たないよう、一覧取得と系列解決の知識境界を`agent-toolkit/agent_toolkit/_common/codex_models.py`に置く。

review・choresの初期値ではCodex候補に`sol/low`を使う。`luna/xhigh`を前二者へ残す案は、ユーザーが速度と精度の釣り合いを見直したため採用しない。これらのモデル名は運用中に変わる設定値であり、テストは工程別候補列の保存と実行時解決の接続を確認する。

Codexの可用性判定で利用上限を観測した場合は、次の候補を先に試す。
全候補が失敗し、そのうちに利用上限の失敗がある場合だけ最も早い解除予定まで待ち、先頭から判定し直す。
解除予定が不明なら300秒後に再確認し、待機の回数と総時間を打ち切らない。
上限判定は共通層がロールアウト記録から行い、選択と待機はorchestratorが持つ。

可用性判定は応答内容を使わず、engine単位の起動の成否と利用上限だけを観測する。
そのためchores_fast_model・review_fast_modelで設定した該当engineの候補（候補がなければClaudeはhaiku、Codexはluna）とeffort `low`で起動する（2026年10月18日、ユーザーの確認回答「最安モデル+軽量」）。
Claudeはセッションを保存せず、ユーザーとプロジェクトの設定・ツール・MCP・スキルを読まず、最小のシステムプロンプトで起動し、process-loopの接続設定の`--settings`は保つ。`--bare`はOAuth認証を読まないため使わない。
Claudeの別名はそのまま渡して最新版へ解決させる。
Codexの系列名はChatGPT認証で系列名のままでは拒否されるため、既存の系列解決で完全IDへ解決し、1回の候補選択の中ではengineごとに1回だけ解決する。
Codexはユーザー設定とrulesを読まずに起動する。利用上限の判定はrolloutを読むため、rolloutを残さない`--ephemeral`は`--json`の出力だけで判定できることを確かめるまで使わない。
候補モデル固有の利用上限とeffortの無視はこの起動形では判定しない。ユーザーはモデル固有の制限は現状まれで、検出のための複雑化を避けるよう求めたため、代わりの検出や本作業の起動失敗時の再選択も加えない。

## process-loopのモジュール構成

`atk wi process-loop`の工程は`agent-toolkit/agent_toolkit/_atk/wi/`配下の責務ごとのモジュールが持つ。作業ツリーの準備と上流との同期は`process_loop_worktree.py`、自己更新の確認と再起動は`process_loop_update.py`、miseの更新は`process_loop_mise.py`が持つ。変更の監視とprivate-notesの同期は`process_loop_watch.py`、アラートの確認は`process_loop_alerts.py`が持つ。プロンプトとオーケストレーターの選択、セッションの起動および担当の終了後のレーン回収は`process_loop_session.py`、無進捗記録と担当の起動要否の判定は`process_loop_launch_gate.py`が持つ。中断要求と追加指示の状態ファイルは`process_loop_control.py`、子プロセスの環境と実行ファイルの解決は`process_loop_env.py`が持つ。`process_loop.py`の`cmd_process_loop`はこれらを順に呼ぶ制御だけを持つ。以前は1つのファイルが全ての責務を持ち、工程を加える計画ごとに同じファイルへ追記していた。モジュール間の呼び出しは定義元のモジュールの属性として行い、テストは定義元を差し替える。テストも同じ単位で`process_loop_<責務>_test.py`へ分ける。

CI失敗の収集・キー重複除外・本文生成・AWI保存は`alerts.py`が持ち、単発の`atk wi check-alerts`と常駐側が同じ本体を呼ぶ。単発は対象作業ツリーの解決と取得・投入結果を報告して終了し、process-wiの1回の実行やDependabot監査を起動しない。`process_loop_alerts.py`は監視間隔とDependabot監査の起動要否を持ち、収集失敗では従来どおり待機を続ける。単発用に収集処理を複製する案は、本文・キー・取得範囲の一致を別々に保守するため採らない。呼び出す時機は各環境が選び、`docs/guide/claude-code-guide.md`「推奨ワークフロー」はその境界を示す。

CI失敗の本文には実行日時、対象commitのSHA、現在のブランチHEADとその比較結果を含める。
HEADを取得できない場合は理由を示す。
古い失敗だけが残る場合は、HEADで実行が作成されない理由と、失敗原因が現行HEADにも残るかを調査する。
原因が残らない場合は確認結果の記録で完了でき、追加変更や後続実行を求めない完成条件を渡す。
GitHub、GitLabのブランチとScheduleの収集が同じ実行事実を渡す。

`_git/forge_cli.py`は`wait_ci.py`と`alerts.py`が使う非対話のログ取得案内を所有する。
案内するコマンドは対象リポジトリを明示し、Git作業ツリー外からも実行できるargvを引用して示す。
GitLabでjobが未確定の場合は、job一覧の取得と対象jobのtrace取得を順に案内する。
収集側と待機側で同じ案内を使い、CLIの対話画面を必要とする手順の分散を防ぐ。

GitLabのリポジトリ識別子とAPI接続先は別に扱う。
SSH接続先はglabのホスト別設定を選ぶキーとし、API接続先はその設定と公開環境指定で解決する。API側を明示上書きする場合はHTTP(S) URLをポート込みでrepositoryへ指定し、baseline作成・待機・再開で同じ指定を使う。
ホスト指定は`GITLAB_HOST`・`GITLAB_URI`・`GL_HOST`の順とし、`GITLAB_API_HOST`の上書きと明示URL・`prefer_environment`の優先関係を保つ。
`_git/remote.py`が接続先を解析し、`_git/forge_cli.py`がglabの子環境へ渡す接続先指定を共通に所有する。
呼び出しごとの子環境は`_common/json_command.py`が受け取り、親環境を保持する。
この接続先を一覧、bridges、downstreamとjobの照会に使い、キュー識別子の正規化とbaselineの保存形式は維持する。

## 開始前の活動猶予とUWI回答待ち

開始可否は常駐側が持ち、`process_loop_watch.py`の変更・期限・中断待機へ接続する。
WI投入作業の活動猶予と未回答UWIの抑止は同時に評価し、待ち時間を順に足さない。
活動猶予は処理可能WIの検出時刻、新規到着の延長は検出時刻、UWIの期限は保存済みファイルのmtimeを使う。
更新の意味が異なるため、全ての時刻を反復の開始時刻へ置き換える案は採らない。
活動がなくなるか10分の期限が来れば活動条件を解除し、新規AWI・UWIの到着ごとに10分へ延長する。
編集・状態移動は同じWI名として扱い、新規到着へ数えない。
UWIは対象集合のinbox・processing・holdの未回答を判定し、最終更新には回答後のadoptedも含める。
holdとrun-skillを抑止へ含めることと処理候補へ含めることは分ける。
全回答か最終更新から600秒でUWI条件を解除し、開始直前に処理可能集合を再評価する。

活動猶予の目的は、ユーザーが投入中のWIの到着を待ってから処理対象を再評価することである。
このため理由にする活動は、実行中の`agent-toolkit:add-awi-by-user`の会話とその委譲先のうち、確定した投入先が対象リポジトリ集合（登録集合）と交わるものに限る。
投入先は同スキルが`atk wi submission begin`で会話別に記録し、変更時に置き換え、完了・中止時に`end`で削除する。
記録はoriginの正規化URLで持ち、同じoriginの別worktreeと別表記のURLを同一リポジトリとして比べる。
起動cwdから投入先を推定する案は、別リポジトリから投入する会話を誤判定するため採らない。
記録はhookが会話の終了で削除する活動JSONと分けて置き、会話を再開した場合は残る記録が再び有効になる。
同じリポジトリの通常の協調作業、process-wi、session-review、別リポジトリへの投入、会話を持たない補助プロセスは理由にしない。
作業ツリーの競合は猶予では防がず、process-loop間の処理所有は`process_loop_ownership.py`が別に持つ。
実行ファイル名と生存PIDだけから活動候補を得る旧方式は、Codexの管理daemonと更新ループで600秒の猶予を生じさせた（2026年10月16日）。
名前別の除外表を増やす案は別の補助処理と無関係な会話を同じ構造で誤判定するため採らず、作業属性を判定の入力にした。
活動一覧の表示（`atk agents list`）は手動起動の親会話の作業種別と投入先を持たないため、判定の入力にしない。

活動の生成は既存hook、投入作業の属性は投入側、取得と親子解決は共通層、開始判定は常駐側に置く。
PIDと起動時刻、実際のターン終了、子の稼働、登録簿を合わせ、当該ループの子孫だけを除く。
終了済みと子なし応答待ちを除外し、属性を持つ会話の読取不能な活動記録は期限付きの活動候補として扱う。
最新状態を更新する有限の記録を使い、全履歴の毎回解析と新しい監視サービスは採らない。
端末と既存ログに理由・期限・延長・開始を残し、子セッションへ待機判断を移さない。

ユーザーがすぐに処理を始めたい場合のために、入力可能な端末では開始前待機をEnterで解除できる。
Enterは活動猶予とUWI猶予を一括で解除し、効力はその回の`wait_for_start_permission`の呼び出しだけに留める。
永続的な解除フラグや別の制御コマンドは置かず、解除後も候補の再評価、同期、worktree準備、処理所有の取得を通常どおり通す。
中断要求はEnterより先に判定して終了を優先し、端末と既存ログには`trigger=enter`付きの解除を残す。
入力の取得は待機の内側に閉じ、待機の間だけ短い間隔で待たずに読み取る。
読取用のスレッドを置く案は、待機後も残って子CLIへ渡る入力を消費するため採らない。
POSIXでは前景の制御端末を`select`で、Windowsでは対話コンソールを`msvcrt`の`kbhit`・`getwch`で読む。
非TTY、閉鎖済み、制御端末の無い端末、EOF、POSIXのバックグラウンドジョブでは入力を読まず、従来の期限と状態変化による待機だけを続ける。
件数0の変更待機、所有者の終了待機、同期・準備失敗時の再試行、モデル利用枠の回復待機はEnterの対象外とする。

## 登録全体の統括と対象別所有

`atk wi process-loop`はprocess_loop_repositoriesの登録全体を扱う唯一の常駐起動形式とする。
旧単一リポジトリ起動、all-reposサブコマンド、--target-repoは廃止する。
常駐cwdで既存run_process_sessionによりorchestrateの対話CLIを1つ起動し、process-wi-all-reposを完遂させる。
Claudeは先頭/goal、Codexは生成目的文を使い、初回と同一会話のresumeで集合JSONを参照する。
通常delegateとして統括を起動する案は、最上位の役割とGoal配送の例外が必要になるため採用しない。
候補数Nに対する既存上限floor((N+9)/10)が2以上の対象だけを統括から対象cwdのprocess-wi担当へ委譲し、それ以外は統括が直接処理する。

常駐が対象別所有の下でworktreeを準備し、凍結した候補、readiness、上限、所有token、lane_session_id、前回情報を渡す。
選定・計画・独立文脈レビュー・公開・CI検収は対象worktreeごとに分ける。
統括は対象の規範とプロジェクトスキルを明示的に読む。
通常のプロジェクトhookは統括cwdでは発火せず、規範と既存チェックで目的を満たす。
WorktreeCreate/Removeは既存atkの対象設定処理を使う。

統括CLI終了後に常駐が対象別結果・キュー・laneの統合とHEADを照合し、残る対象だけを同じ会話へ継続させる。
対象別の無進捗を2回連続で観測した場合と続行不能の場合は、その対象の入力を無進捗記録へ保存する。
同じ入力では再起動せず、WI・診断・アラートの変化または明示resumeを待つ。
記録はホストの対象別stateに置き、別ホストのWIをholdしない。
統括IDと対象別所有を分け、ある対象の進捗で他対象のカウントを解除しない。
統括と配下writerの終了を確認してから所有解放と既存laneを回収し、停止未確認、未統合、dirty、使用中は保持する。
登録・候補・回収の契約は[all-repos-orchestration.md](../../agent-toolkit/skills/process-wi/references/all-repos-orchestration.md)が所有する。

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

`update-dotfiles`は同じPython環境で専用ヘルパーを1回起動し、旧退避の分類と保護をGitの一括処理へ渡す。
保存した作業内容・index・未追跡内容がrootの`mise.lock`だけであり、退避元commitがbranch・remoteから到達できる旧退避は回収する。
その他の退避と、ローカルcommitの唯一の参照になっている退避は保持し、共有保護が無いものだけ保護する。
固定ロック内で読み取ったOIDを条件に、回収と保護を`git update-ref --stdin`のトランザクションで確定する。
履歴を保持するbranch・remoteのOIDも同じトランザクションで検証し、到達性判定の後に参照が移動した場合は回収を中止する。
保護済みの退避を1件ずつ再起動・再表示する処理と、移行済みを表す専用の永続フラグは置かない。
画面は整理が発生した場合の件数要約と、失敗した場合の原因・次の操作を示す。個別の識別子と処理の詳細は永続ログが保持する。
欠損や保護値の不整合ではデータを保持してpullへ進まない。

今回退避は`stash apply --index`の成功後だけ`drop <ref>`で回収する。pull失敗後の復元成功も回収し、復元競合とrebase競合では保持する。回収失敗は復元失敗と分け、復元済み内容をresetせず再試行を案内する。dropは固定ロック内で読取OIDを条件として固有ref、共有保護refの順に削除し、片側だけ残っても同じ識別子で再試行できる。他worktreeの同label退避と既存共有stashは回収しない。
`git pull --autostash`への単純置換は、未追跡内容・indexの保全と競合後の回復を満たさないため採用しない。
退避の作成・復元・回収は既存のGit操作を使い、旧退避の整理のために新しい管理サービスや台帳は追加しない。

Claude Codeの`--worktree`へ置き換える案は、worktree隔離ガードがシェル構文を拒否するため採用しない。
`atk`側でworktreeを準備し、セッションのcwdを準備済みworktreeへ設定する。
対象にWorktreeCreate/Removeがあれば共通のhook呼出しを使い、対象設定・cwd・イベント入力・環境を渡す。
返却先とGit登録・branch・共通ディレクトリ・資源所有を一致させ、設定済みhookの失敗では途中資源と診断を保持する。
通常Git作成への暗黙の切替は、対象が指定した準備・撤去の対応を失うため採らない。
hookが無い対象は固定配置と対象固有のprepareを使い、hook指定先も反復ごとに別名へ回避しない。
常駐が起動前にworktreeを準備し、統括は対象の規範と準備結果を利用する。

## process-loopの会話IDによる識別

process-loopがClaude会話を新規に起動するときは`--session-id`で会話IDを指定し、同じIDを子環境へ渡す。ID指定の再開では指定値を渡す。hookは環境印とhook入力の`session_id`の一致から対象会話を決める。入れ子の`claude`は環境印を継承する一方で会話IDが異なるため、常駐本体の終了保証と空転ガードの対象にならない。IDを指定しない再開ではprocess-loopが対象IDを渡せないため、環境印による従来の判定を保つ。親プロセスの探索はプロセス階層とOSを前提とする条件を増やし、hook入力に既にある会話IDより間接的な判定になるため採用しない。

## process-loopの中断要求

`atk wi process-loop abort`は中断要求をOSアカウントの状態ディレクトリにある`process-wi-abort`の存在で表す。
process-loopは実行中のセッションが終了した直後に要求を確認し、次の反復へ進まず正常終了する。
終了時は端末ベルを3回鳴らし、各鳴動を端末が区別できるよう短い間隔を置く。
中断要求は終了時に消費し、次回のprocess-loopを通常状態で開始する。

状態ファイルは`process-wi.log`の親ディレクトリへ配置する。
これにより、process-loopと操作コマンドは`XDG_STATE_HOME`を含む同じOSアカウントの状態の解決規則を共有し、対象リポジトリやprivate-notesの初期化を必要としない。
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
親の`process_loop_session.py`は子の終了後に`lane.cleanup_session`で回収を試み、未統合・dirty・稼働中・確認不能などのレーンは所有登録と成果を保持する。実体が全て無い登録は同じ処理が回収するため、固定作成先を次のprocess-wiが再利用できる。
レーンのbranch・worktree・選定原本・旧所有sessionは、`lane.py`が状態ディレクトリの`lanes/<旧所有session>/lane-*.json`へ保存する所有登録が持つ。
所有登録は選定原本から写したレーンのWI集合と統合状態、rebase後の対応表と検証記録も持つため、managed-tempにある選定原本が失効した後の再開と回収も同じ所有登録から判定する。
状態ディレクトリの絶対パスは`atk config get state_dir`で取得する。
WIの本文と`processing`の状態はprivate-notesのキュー、計画と進捗ログは`~/.claude/plans`が保持する。
新しい会話のprocess-wiは[選定手順](../../agent-toolkit/skills/process-wi/references/selection-procedure.md)に従い、`atk lane delete --list`で残存登録と回収結果を読み、WIに対応する計画を`atk plans list`から解決する。
再開する工程は計画の進捗ログから決め、[中断レーンの再開](../../agent-toolkit/skills/process-wi/references/run-lanes.md#中断レーンの再開)に従って新しいレーン担当へ計画と既存worktree・branch・現在HEADを渡す。
資源を再利用するときは旧所有主体の終了を確認し、`atk lane create`の`--reuse-record <record_path>`へ所有登録のパスを渡して新しい所有sessionへ移す。
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
追加指示は起動集合の統括担当へ1回渡し、統括が対象に関係する部分を適用する。
AWIが0件で変更検知を待つ反復は起動へ到達しないため、本文は保持したまま次の起動へ残る。
`rules_context`のSessionStartは、`AGENT_TOOLKIT_PROCESS_LOOP_INSTRUCTION`に値がある場合に前置きを添えて`additionalContext`へ加え、
委譲先のセッションへは加えない。この本文がユーザーの入力であり、人間由来の明示的な指示であると前置きに明記する。
本文は`forwarded-user-input`要素で囲み、`agent-toolkit/rules/01-agent.md`「方針が衝突する場合の優先順位」がこの要素の内側だけを機械が生成した本文の中のユーザー発話の証拠として扱う。

本文の上限を2,000文字とするのは、SessionStartの`additionalContext`がClaude Codeで10,000文字に切り詰められ、
超過すると同じ出力に含まれる規範条文が欠落するためである。
`/goal`の目的文へ連結する案は、目的文がターンを終えるたびの目標評価の入力となるため採用しない。
起動後のセッションが自ら状態ファイルを読む案は、全セッションへ読取の1工程を課し、
かつセッションが手順を実行する必要があるため採用しない。

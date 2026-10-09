# 継続処理の起動・隔離・制御の設計記録

本書は[設計記録の索引](design.md)から主題別に分割した記録であり、機構の目的、構造の理由、知識境界と却下した代替案を保持する。
実行時に適用する規範は、各節が参照する現行のルールファイルとスキルが定める。

## process-loopのオーケストレーター選択

`atk wi process-loop`の常駐セッションで使用するengine・model・effortは、永続設定の
`orchestrate_model`キーで一元的に決定する。設定値は工程別モデル設定と共通の
`<claude|codex>:<model>[/<effort>]`をASCIIカンマで区切った候補列である。
値は`agent-toolkit/agent_toolkit/_atk/config.py`の`resolve_mutable_setting`が、環境変数`AGENT_TOOLKIT_CONFIG_ORCHESTRATE_MODEL`、保存値、未設定時の値の順に取得する。
未設定時の値は同モジュールの`_MUTABLE_KEY_DEFAULTS`が`_preset_settings("codex-balanced")`から導出する`claude:opus[1m]/medium,codex:sol/medium`である。

`agent-toolkit/agent_toolkit/_atk/wi/process_loop.py`の`cmd_process_loop`は開始時に`process_loop_session.py`の`resolve_orchestrator_specs()`で設定値を検証する。
さらに子セッションを起動する前ごとに同関数で候補列を読み直し、`select_available_orchestrator`が先頭の候補から可用性を判定して最初に利用できる候補で起動する。
このため常駐中に`atk config set`で保存した変更は、次の子セッションの起動前に反映される。
空でない環境変数がある間は、保存値を変えても環境変数の値が実効値になる。
候補列を子セッションの起動前ごとに読み直すため、可用性とCodex系列名の解決は起動時点の利用枠とモデル一覧で判定され、設定の変更も常駐プロセスを再起動せずに次の子セッションへ届く。

CLI引数の`--orchestrator`・`--model`を設定と併存させる案は、設定との優先関係を複雑化するため
ユーザー合意で廃止した。

Codexの`astra`・`sol`・`terra`・`luna`は設定へ系列名として保存する。完全IDへの解決は`resolve_orchestrator_specs()`内の`resolve_model_candidates("orchestrate")`が子セッションの起動前ごとに行い、App Serverの`model/list`を最終ページまで取得して表示対象の同系列の最新版へ解決する。委譲起動は保持中のApp Server接続を使い、設定CLIは短命の接続を所有する。選んだ完全IDが指定effortを受理しない場合は理由を返し、別系列または旧版へ暗黙に置換しない。明示された完全IDは固定し、engine側の可用性判定が返す診断を候補とともに示す。Claude候補の可用性判定は構造化出力（`stream-json`）で起動し、Weekly limitか5時間の利用上限による拒否を出力の利用枠情報から判定する。この拒否では次の候補へ進まず、解除予定時刻（不明なら300秒後）まで待って同じ候補を判定し直し、待機の種類と解除予定時刻を端末とprocess-loopのログ（`usage_limit_wait`）へ出力する（ユーザー指示）。本作業のセッションが利用上限で終わった場合も、次の反復の可用性判定が同じ待機へ入る。モデルを試行起動して系列の最新版を推測する案は、実行費用を生み、利用可能なモデル一覧との対応も保証できないため採用しない。複数の起動処理が同じ判断を持たないよう、一覧取得と系列解決の知識境界を`agent-toolkit/agent_toolkit/_common/codex_models.py`に置く。

工程別モデル設定の中位（`medium_tier_model`）で値を保存していない場合に使うCodexの候補と、プリセットの「軽量」「探索上位」は`terra/medium`を選び、「上位」と「探索軽量」は各用途の指定を維持する。`luna/xhigh`を前二者へ残す案は、ユーザーが速度と精度の釣り合いを見直したため採用しない。これらのモデル名は運用中に変わる設定値であり、テストは値の複製ではなく、プリセットの導出、engine順、保存と実行時解決の接続を確認する。

## process-loopのモジュール構成

`atk wi process-loop`の工程は`agent-toolkit/agent_toolkit/_atk/wi/`配下の責務ごとのモジュールが持つ。作業ツリーの準備と上流との同期は`process_loop_worktree.py`、自己更新の確認と再起動は`process_loop_update.py`、miseの更新は`process_loop_mise.py`が持つ。変更の監視とprivate-notesの同期は`process_loop_watch.py`、プロンプトとオーケストレーターの選択とセッションの起動は`process_loop_session.py`、アラートの確認は`process_loop_alerts.py`が持つ。中断要求と追加指示の状態ファイルは`process_loop_control.py`、子プロセスの環境と実行ファイルの解決は`process_loop_env.py`が持つ。`process_loop.py`の`cmd_process_loop`はこれらを順に呼ぶ制御だけを持つ。以前は1つのファイルが全ての責務を持ち、工程を加える計画ごとに同じファイルへ追記していた。モジュール間の呼び出しは定義元のモジュールの属性として行い、テストは定義元を差し替える。テストも同じ単位で`process_loop_<責務>_test.py`へ分ける。

CI失敗の収集・キー重複除外・本文生成・AWI保存は`alerts.py`が持ち、単発の`atk wi check-alerts`と常駐側が同じ本体を呼ぶ。単発は対象作業ツリーの解決と取得・投入結果を報告して終了し、process-wiの1回の実行やDependabot監査を起動しない。`process_loop_alerts.py`は監視間隔とDependabot監査の起動要否を持ち、収集失敗では従来どおり待機を続ける。単発用に収集処理を複製する案は、本文・キー・取得範囲の一致を別々に保守するため採らない。呼び出す時機は各環境が選び、ガイド「推奨ワークフロー」はその境界を示す。

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
`atk`側でGit worktreeを準備し、セッションのcwdを準備済みworktreeへ設定する。

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

## process-loopへの1セッション限定の追加指示

`atk wi process-loop instruct <本文>`はprocess-loopが次に起動する1セッションだけへ渡す本文を、
中断要求と同じOSアカウントの状態ディレクトリの`process-wi-instructions`へ追記する。
`instruct-cancel`は保持中の全件を破棄し、`status`は中断要求の有無とあわせて保持中の件数と本文を表示する。
完全一致する本文の再投入は誤操作とみなして追記せず、保持中の合計が2,000文字を超える投入は拒否する。

process-loopはセッションを実際に起動する直前に状態ファイルを読み取って削除し、
得た本文を`AGENT_TOOLKIT_PROCESS_LOOP_INSTRUCTION`として子セッションの環境へ載せる。
AWIが0件で変更検知を待つ反復は起動へ到達しないため、本文は保持したまま次の起動へ残る。
`rules_context`のSessionStartは、`AGENT_TOOLKIT_PROCESS_LOOP_INSTRUCTION`に値がある場合に前置きを添えて`additionalContext`へ加え、
委譲先のセッションへは加えない。この本文がユーザーの入力であり、人間由来の明示的な指示であると前置きに明記する。
本文は`forwarded-user-input`要素で囲み、`agent-toolkit/rules/01-agent.md`「方針が衝突する場合の優先順位」がこの要素の内側だけを機械が生成した本文の中のユーザー発話の証拠として扱う。

本文の上限を2,000文字とするのは、SessionStartの`additionalContext`がClaude Codeで10,000文字に切り詰められ、
超過すると同じ出力に含まれる規範条文が欠落するためである。
`/goal`の目的文へ連結する案は、目的文がターンを終えるたびの目標評価の入力となるため採用しない。
起動後のセッションが自ら状態ファイルを読む案は、全セッションへ読取の1工程を課し、
かつセッションが手順を実行することに依存するため採用しない。

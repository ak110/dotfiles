# process-loopのモジュール構成の設計記録

[設計記録の索引](design.md)と[継続処理の起動・隔離・制御の設計記録](design-process-loop.md)から参照する主題別の記録である。

## process-loopのモジュール構成

`atk wi process-loop`の工程は`agent-toolkit/agent_toolkit/_atk/wi/`配下の責務ごとのモジュールが持つ。
作業ツリーの準備と上流との同期は`process_loop_worktree.py`、自己更新の確認と再起動は`process_loop_update.py`、miseの更新は`process_loop_mise.py`が持つ。
変更の監視とprivate-notesの同期は`process_loop_watch.py`、アラートの確認は`process_loop_alerts.py`が持つ。
プロンプトとオーケストレーターの選択、セッションの起動および担当の終了後のレーン回収は`process_loop_session.py`、無進捗記録と担当の起動要否の判定は`process_loop_launch_gate.py`が持つ。
中断要求と追加指示の状態ファイルは`process_loop_control.py`、子プロセスの環境と実行ファイルの解決は`process_loop_env.py`が持つ。
`process_loop.py`の`cmd_process_loop`はこれらを順に呼ぶ制御だけを持つ。
以前は1つのファイルが全ての責務を持ち、工程を加える計画ごとに同じファイルへ追記していた。
モジュール間の呼び出しは定義元のモジュールの属性として行い、テストは定義元を差し替える。
テストも同じ単位で`process_loop_<責務>_test.py`へ分ける。

CI失敗の収集・キー重複除外・本文生成・AWI保存は`alerts.py`が持ち、単発の`atk wi check-alerts`と常駐側が同じ本体を呼ぶ。
単発は対象作業ツリーの解決と取得・投入結果を報告して終了し、process-wiの1回の実行やDependabot監査を起動しない。
`process_loop_alerts.py`は監視間隔とDependabot監査の起動要否を持ち、収集失敗では従来どおり待機を続ける。
単発用に収集処理を複製する案は、本文・キー・取得範囲の一致を別々に保守するため採らない。
呼び出す時機は各環境が選び、`docs/guide/claude-code-workflows.md`「推奨ワークフロー」はその境界を示す。

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
SSH接続先はglabのホスト別設定を選ぶキーとし、API接続先はその設定と公開環境指定で解決する。
API側を明示上書きする場合はHTTP(S) URLをポート込みでrepositoryへ指定し、baseline作成・待機・再開で同じ指定を使う。
ホスト指定は`GITLAB_HOST`・`GITLAB_URI`・`GL_HOST`の順とし、`GITLAB_API_HOST`の上書きと明示URL・`prefer_environment`の優先関係を保つ。
`_git/remote.py`が接続先を解析し、`_git/forge_cli.py`がglabの子環境へ渡す接続先指定を共通に所有する。
呼び出しごとの子環境は`_common/json_command.py`が受け取り、親環境を保持する。
この接続先を一覧、bridges、downstreamとjobの照会に使い、キュー識別子の正規化とbaselineの保存形式は維持する。

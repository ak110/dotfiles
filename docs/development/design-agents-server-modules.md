# 実装配置と共有状態の非同期境界の設計記録

[設計記録の索引](design.md)から参照する、実装配置と共有状態の非同期境界の記録である。

## agents_serverの実装の配置

目的はMCPツールの公開面、sessionの管理、engine別のbackendおよび共有状態の読み書きを責務ごとのモジュールへ分けることである。
変更が1つの責務の範囲で完結するようにする。
`agent-toolkit/agent_toolkit/agents_server_mcp.py`はMCPホストの設定が起動するパスである。
同ファイルは引数の解釈と`_agents_server/mcp_tools.py`の`serve`の呼び出しだけを持つ。
MCPツールの定義とlifespanは`mcp_tools.py`が持つ。
initializeの節目の診断ログと、例外のエラー本文への変換は`mcp_transport.py`が持つ。
ツールと引数の説明文は`tool_descriptions.py`、`start`の入力の検証と`<役割名>.subagent.md`の解決は`launch_requests.py`が持つ。

`AgentsServerManager`（`manager.py`）は責務ごとのクラスを順に継承し、起動・追送・中断・終了を加える。
継承する順は、共有の状態とbackendの保持（`manager_base.py`）、応答の整形（`responses.py`）、session登録簿からの解決と一覧・表示・停止（`manager_registry.py`）である。
その後に自動再開（`manager_resume.py`）と待機（`manager_wait.py`）が続く。
各責務が同じsession集合と条件変数を共有するため、状態を持つ1つのインスタンスの中でモジュールだけを分ける。
責務ごとに別のオブジェクトへ分け、状態を引数で渡し合う案は採らない。
session集合・待機中の再開・条件変数を全てのオブジェクトが参照するため、受け渡しの箇所が増えるだけで依存は減らない。

engine別のbackendは`backends.py`の`Backend`（`typing.Protocol`）を満たす。
engineごとの性質は各backendのクラスが属性と静的メソッドで持ち、manager系のモジュールはengine名を比べずに問い合わせる。
対象の性質は、起動の例外で次の候補へ進むか、可用性の失敗の判定、中断にturnの識別子を要するか、所有者のいない記録を引き継げるかである。
engine名とbackendのクラスの対応と、engineごとに異なる生成時の引数を持つのは`backends.py`だけである。

共有状態ディレクトリの読み書きは、対象ごとのモジュールへ分ける。
配置は`shared_layout.py`、ルートと書込主体の識別は`shared_roots.py`、statusline向けの状態ファイルは`status_file.py`が持つ。
終了時の結果は`retained_results.py`、待機対象は`wait_targets.py`、上り通知は`notice_inbox.py`、除外した候補は`unavailable_candidates.py`、掃引は`shared_sweep.py`が持つ。
session状態は`state.py`、公開応答への射影は`result_projection.py`、自動再開の状態遷移は`resume_waits.py`が持つ。
委譲先の待機の観測は`wait_output_tracking.py`、委譲先へ渡す指示の組み立ては`launch_prompts.py`が持つ。

### 共有状態の非同期境界

イベントループのスレッドで同期のロック取得・Event待機・join・I/Oの完了を待つと、同じloopの独立した要求、通知、heartbeatと停止要求を処理できない。
このため、同期操作をworkerへ分離し、loop側はawaitで完了を待つ。
保存と監視の完了を待つのは、登録・保存の順序と終了時の結果の一度だけの回収を保ち、停止時に全threadの終了と出力の完了を確認するためである。
待機を打ち切る案と、保存や監視を背景化したまま返る案ではこれらの完了を保証できないため採らない。

同期操作の共通実装は`_common/async_operations.py`が持つ。
agents_serverは`file_operations.py`の既存名から利用し、serveの監視とmedia_remoteの監視も同じ実装で準備・終了を回収する。

Managerの照会・復元・回収は非同期APIで提供する。
登録簿、候補除外、終了時の結果と通知の排他回収、起動資料、設定合成、診断と資源照会は`file_operations.run_file_operation`でworkerの完了を待つ。
同期CLI向けのファイル操作APIも保持する。
SessionStateの更新、射影の組み立て、公開済み結果の台帳と保持タイマーはイベントループが所有する。
workerへは操作に必要なパス・本文と、資源計測用の状態snapshotを渡す。
終了では予約を取り消し、開始済みの保存・診断とbackendの後始末を回収する。
Claudeの接続終了と設定の回収は1つの保護されたtaskで完走する。

永続ログは専用workerが受理順に出力し、サーバーの返却前に全記録とworkerの終了を回収する。
受信側handlerとファイルhandlerのロックを分け、Pythonのlogging終了処理がhandlerのロックを保持していてもworkerを回収できるようにする。

### atkの起動形と観測

`_common/shell_tokens.py`がランチャー名と起動の事実を記録するシェルツールの集合を所有する。
案内側の`cli_instructions`と起動の判定側は同じ定義を使う。
WindowsのPowerShellで実行した`atk agents wait`もPostToolUseが観測の試みとして記録し、`pending_observation`を解消する。
待機の出力追跡は分解済みの実行トークンから起動を判定し、引数へ引用されたコマンドの文字列を実行の根拠へ含めない。

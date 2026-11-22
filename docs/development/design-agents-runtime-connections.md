# 接続選択とAPI残高不足の設計記録

[設計記録の索引](design.md)

## API失敗・過負荷・利用上限の待機

役割別のClaude接続選択では、API環境ファイルが持つ認証・接続・モデルの全キーを選択入力とする。
子が自ら読むuser・project・local設定の`env`が同じ変数を定義する場合だけ、CLIのsettingsへその変数を空の値で指定する。
settingsは変数を削除できないため、この場合の空の値は子のコマンドにも残る。
新規・再開・接続切替は同じ解決を使い、未設定の通常認証と設定解除後の復帰を保つ。
一部の認証キーだけを除く案は、親の接続先とモデル指定が選択後の接続へ漏れるため採用しない。
秘匿値は診断へ保存しない。

process-loopとrun-skillは上限で中断した旧CLIの終了を確認し、同じ会話を利用可能な次の接続で再開する。
接続の代替がない場合に既存の待機設定を適用し、再開のために別engineの会話を作らない。
手動resumeでも可用性を再評価するため、古い上限の判定を永続化して接続を除外する台帳は設けない。
process-loopのStopFailure要求は再評価の契機として使い、要求ファイルだけから上限種別を推定しない。
modの予約と起動側の再開が競合しないよう、process-loopとrun-skillの会話は起動側が再開を所有する。

委譲先の使用順でAPIを先頭に置く設定では、API残高が不足している間に起動する新しい委譲先が毎回APIで失敗し、サブスクリプションへの切替と再開の往復を繰り返していた。
そこで`credits`接続のturnが残高不足で失敗した時刻をagents_serverのプロセス内メモリーへエンジン別に記録し（`_agents_server/api_credits.py`）、1時間以内に起動する委譲先では`credits`候補を使用順の後ろへ回す。
候補からは外さず、オーケストレーターの使用順と利用上限の切替は変えない。
Codexも失敗した候補から次へ移る同じ構造を持つため、同じ並べ替えを適用する。
Codex CLI 0.165.0のApp Serverの公開スキーマ（`codex app-server generate-json-schema`の`CodexErrorInfo`）は残高不足を区別した値を持たないため、`credits`候補の不受理を残高不足として記録する。
観測を永続化する案は採用しない。
API残高は月1回の付与などで外部から回復し、永続化した判定ではその回復を拾えないためである。
1時間ごとにAPIを再び試すことで回復を確かめる（ユーザー要件）。
記録はMCPサーバーのプロセスごとに独立し、再起動すると通常の接続順へ戻る。

Claude SDKの初期化応答がMCP接続失敗を示す場合、新規起動・再開ごとに公開再接続APIを1回試す。
再接続の反復を独自に増やす案は、SDKの回復境界へ別の待機を重ねるため採用しない。
再検証の試験は`agent-toolkit/agent_toolkit/_agents_server/claude_connections_test.py`にある。
`test_mcp_reconnect_is_once_per_new_and_resumed_process`と`test_slow_mcp_recovery_keeps_start_budget_and_updates_show`を実行する。
この試験はテストダブルによる契約の検証であり、実Claude CLIと外部MCPサーバーの接続障害は再現していない。

Codexの接続先は、役割別の使用順（`codex_orchestrator_connections`・`codex_worker_connections`）と種別ごとのprovider IDから決める。
委譲先は待機対象外のサブスク利用上限とAPIの不受理で、状態互換性を確認して使用順の次の候補へ同じ会話を移す。
統括担当は会話の中で切り替えない。
4キーのどれも設定しない場合はCodex通常設定に従い代替試行しない。
`codex` CLIで起動するprocess-loopの統括担当は、可用性判定（`--ignore-user-config`を付けた`codex exec`）をproviderごとに行う。
可用性判定は`config.toml`を読まないため、本起動が読む`config.toml`の`model_providers.<ID>`の定義を`-c`で同じ判定へ渡し、判定と本起動で同じ定義を評価させる。
判定だけ`--ignore-user-config`を外す案は、ユーザー設定のMCPやrulesを判定へ持ち込むため採らない。
切替のための新しい上限台帳や監視は設けず、既存の監視と再開情報を使う。
APIへ移った会話の再開は新規起動の回復判定から独立させる。
接続先判定とcold resumeの詳細は[design-agents-server-connections.md](design-agents-server-connections.md#起動候補と観測identity)にある。

API残高不足の観測を永続化せず定期的に確かめ直すことは、2026年10月の実会話でユーザーが求めた。
1時間の保持はエージェントの判断で、専用の初期WIは特定できていない。
判定は維持とし、接続と認証の選択権は設定側に保ち、認証の失敗を隠すことと認証情報の複製はしない。

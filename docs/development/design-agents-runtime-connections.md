# 接続選択とAPI残高不足の設計記録

[設計記録の索引](design.md)から参照する、接続選択とAPI残高不足の記録である。

## API失敗・過負荷・利用上限の待機

役割別のClaude接続選択では、API環境ファイルが持つ認証・接続・モデルの全キーを選択入力とする。
サブスクリプション起動では親環境と親settingsから同じキーを除き、API起動では選択したファイルの値を渡す。
CLI起動では起動環境から除き、SDK起動ではagents_serverの起動時に自プロセスの環境から除く。
除去前の値を保持し、inheritのClaudeとCodexのApp Serverへだけ必要な値を戻す。
合成元の親settingsの`env`からも隔離対象を除く。
子が自ら読むuser・project・local設定の`env`が同じ変数を定義する場合だけ、CLIのsettingsへその変数を空の値で指定する。
settingsは変数を削除できないため、この場合の空の値は子のコマンドにも残る。
新規・再開・接続切替は同じ解決を使い、未設定の通常認証と設定解除後の復帰を保つ。
一部の認証キーだけを除く案は、親の接続先とモデル指定が選択後の接続へ漏れるため採用しない。
秘匿値は診断へ保存しない。

Claudeの統括CLIは、`_atk/orchestrator.py`が役割別の接続候補を評価し、probeで成功した接続を本起動へ渡す。
process-loopとrun-skillは上限で中断した旧CLIの終了を確認し、同じ会話を利用可能な次の接続で再開する。
接続の代替がない場合に既存の待機設定を適用し、再開のために別engineの会話を作らない。
手動resumeでも可用性を再評価するため、古い上限の判定を永続化して接続を除外する台帳は設けない。
process-loopのStopFailure要求は再評価の契機として使い、要求ファイルだけから上限種別を推定しない。
run-skillは公開`--resume`で元の入力・実行ID・batch範囲・残時間を復元する。
modの予約と起動側の再開が競合しないよう、process-loopとrun-skillの会話は起動側が再開を所有する。
配下の復旧は[親復帰時の子孫復旧](design-agents-server-identity-recovery.md#親復帰時の子孫復旧)と同じManager・登録簿を使う。

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
SDK 0.2.168の`get_mcp_status`と`reconnect_mcp_server`を使い、未回復のサーバーだけをstart・showのwarningsへ返す。
ログは`claude_mcp_recovered`と`claude_mcp_recovery_failed`で回復の成否を示す。
再接続の反復を独自に増やす案は、SDKの回復境界へ別の待機を重ねるため採用しない。
監査記録と再検証手段は`audit-records-agents-server.md`の「docs/development/design-agents-runtime.md：MCP接続失敗の再接続：2026年10月20日」にある。

接続選択のログは`claude_connection_attempt`・`claude_connection_selected`・`claude_connection_failed`を使う。
`phase`は`initialization`または`turn`とし、turnの理由は`authentication`・`api_credits`・`usage_limit`で区別する。
接続切替は`claude_api_switch`へ`previous`・`selected`・`reason`・`limit_type`を記録する。
いずれも選択と診断を示す情報であり、認証値は記録しない。

Codexの接続先は、役割別の使用順（`codex_orchestrator_connections`・`codex_worker_connections`）と種別ごとのprovider IDから決める。
provider IDは`codex_credits_provider`・`codex_payg_provider`が持ち、`subscription`は`openai`とする。
新規の会話は使用順の先頭の利用可能な候補を主接続先とする。
委譲先は待機対象外のサブスク利用上限とAPIの不受理で、状態互換性を確認して使用順の次の候補へ同じ会話を移す。
統括担当は会話の中で切り替えない。
4キーのどれも設定しない場合はCodex通常設定に従い代替試行しない。
保持したproviderの種別を使用順から外した会話は、次の継続のcold resumeで使用順の先頭の利用可能な候補へ移す。
`codex` CLIで起動するprocess-loopの統括担当は、可用性判定（`--ignore-user-config`を付けた`codex exec`）をproviderごとに行う。
最初に成功したproviderを本起動と再開へ公開オプション`-c model_provider=<ID>`で渡し、セッション登録簿へ保存する。
可用性判定は`config.toml`を読まないため、本起動が読む`config.toml`の`model_providers.<ID>`の定義を`-c`で同じ判定へ渡し、判定と本起動で同じ定義を評価させる。
判定だけ`--ignore-user-config`を外す案は、ユーザー設定のMCPやrulesを判定へ持ち込むため採らない。
終了時の結果は既存の保留機構へ置き、MCPの監視が終了済みthreadのcold resume後に、
以前の会話と実行結果を確認して完了済み操作を繰り返さず未完了部分を続ける入力を送る。
未観測の孫や背景出力が残る場合はその待機を優先し、結果の帰属を維持する。
切替のための新しい上限台帳や監視は設けず、既存の監視と再開情報を使う。
APIへ移った会話の再開は新規起動の回復判定から独立させる。
接続先判定とcold resumeの詳細は[design-agents-server.md](design-agents-server-connections.md#起動候補と観測identity)にある。

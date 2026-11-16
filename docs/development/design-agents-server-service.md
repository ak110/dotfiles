# 共有管理サービスと世代移譲の設計記録

[設計記録の索引](design.md)から参照する、MCP接続と管理・実行資源の寿命を分ける記録である。

## 接続と管理の分離

同じOSアカウントと状態領域の接続は、共有管理サービスを使う。
`service_forward.py`はstdioとローカルIPCを転送し、公開6操作のスキーマ・説明・応答は既存の`mcp_tools.py`が所有する。
転送側へManager、backend、MCP SDKを読み込む方式は採らない。
接続ごとの管理処理と監視の重複を減らし、接続切断だけで委譲先を停止しないためである。

`service_context.py`は会話IDごとのManagerを保持する。
`CallerContext`が呼出元の環境、cwd、親settingsとホストのPID世代を明示し、非同期タスクにも同じcontextを束縛する。
要求の`_meta.threadId`を優先し、同じ会話への複数接続は合流する。
別会話の設定・認証候補・クレジット不足の観測を混ぜず、サービス起動者の環境で不足値を補わない。
認証値はIPC内の非永続contextに限り、世代登録・ログ・再開情報へ保存しない。
effort、release、heartbeatの巡回はサービスへ集約する。
接続EOFは接続だけを外し、実ホストの死亡はPIDと起動世代で判定する。

## 管理領域と通信

管理領域は`state_dir/services/agents-server`とする。
`service_generations.py`の`generations.json`は受付先、コード実体・版、OSプロセス世代と準備順を登録する。
実行ジョブの台帳は既存のセッション登録簿を使う。
`startup.lock`、`publish.lock`、`generations.lock`は固定名の排他ファイルとして保持し、世代ごとにreadyのJSONと標準エラーログを置く。
同版の準備は合流し、遅れて成功した旧準備で受付先を戻さない。

LinuxはAF_UNIXのabstract socketを使い、環境や操作本文を送る前にSO_PEERCREDで相互のUIDを照合する。
接続側は発見記録のPID世代も確認する。
Windowsは現在SIDだけを許可する明示DACL付きnamed pipeを使い、remote接続を拒否する。
peer PIDの起動世代とSIDを確認し、不一致やAPI失敗では接続を閉じる。
発見記録だけの認証、無認証loopback、独自の共有秘密を保存する方式は採らない。

## 受付・状態writer・実行資源の移譲

受付先、会話の制御所有者、raw資源の生成元は別々に扱う。
raw資源は元世代が維持するSDK・App Server接続、生イベントはその接続からの未解釈イベントを指す。
`service_coordinator.py`は新turnの受付済み予約を回収し、射影と登録の保存を静止してから現行世代の単一writerへ切り替える。
`service_projection.py`と`service_registry.py`は旧側からの保存を現行writerへ転送する。
実行中turnの解釈と制御は旧世代に残し、同じhostへ世代別のwriterを並立させない。

旧backendの実turn終了と保存完了後に、全`SessionState`、待機圧縮などの補助状態、raw資源参照を新世代へ移す。
新世代が以後の解釈・保存・次入力を所有し、旧世代は生I/Oとイベントだけを提供する。
受付先の変更でraw資源のoriginは付け替えない。
子やバックグラウンドタスクが終了し、次の本来の入力を送る境界では通常のreleaseを経て新SDK・App Serverでresumeし、元の資源を回収する。
移譲のためのquery、中断、追加promptは送らない。
3世代以上でも次入力時の現行世代を再解決し、中間世代を経由するだけのモデル呼び出しを作らない。

所有移譲のcommit応答が失われた場合は同じ移譲IDの受理状態を照会する。
移譲先も消失して成否を確定できなければ、安全側に閉じて成否不明を返す。
旧decoderの再開や無条件再送で二重実行を起こさず、既存登録簿とbackendの物理記録による復旧へ渡す。
健康な旧raw資源の提供者を`owner_lost`障害として扱わず、実障害時の所有確認・保留・未回収結果の保持条件を維持する。

## 寿命と初回移行の境界

定常時の管理面は、接続数Nに対して接続ごとのuvと転送、および共有の診断supervisorとserviceで2N+2プロセスとなる。
backendプロセスと更新中の旧世代は別に数える。
既存supervisorが依存環境の使用lock、16KiBの標準エラー末尾と終了診断を所有し、serviceは保持plugin rootのleaseを持つ。
旧世代は担当会話、受付処理、保存ACK前イベント、raw資源が残る間は保持し、期限だけでは停止しない。
Windowsでは元handleによるsuspended生成、Job所属、再開の順を守り、起点の終了とJob全体の回収を分ける。

共有サービスを持たない旧stdioからの初回移行は、起動中の会話が自然終了するまで旧接続を保持する。
旧版にはRPCと状態exportがなく、Claude Code・Codex双方の自動再接続も未観測であるため、会話途中の暗黙移譲はこの初回範囲では達成できない。
旧stdioへSIGTERMを送らず、旧側の待機・通知・結果回収を残し、新しい通常MCP起動から共有サービスを使う。
共有サービス導入後の更新には前節のturn終了による暗黙移譲を使う。

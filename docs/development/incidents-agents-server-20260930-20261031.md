# 障害・欠陥の記録: agents_serverと委譲基盤の不全（2026年9月30日〜2026年10月31日）

[agents_serverと委譲基盤の不全](incidents.md#agents_serverと委譲基盤の不全)の事例を発生時期別に収録する。

- 2026年9月30日: 工程別設定`codex:sol/medium,claude:opus[1m]/medium`の`start`が、`sol`系列の最新版として解決した`gpt-6.1-sol`で起動し、接続先がそのモデルを提供しないため最初のturnが失敗した。
  直接原因: 可用性失敗の判定が`codexErrorInfo`の利用枠超過・流量制限・過負荷の区分とHTTPステータスだけを判定材料としており、モデルIDの不受理はどちらにも当たらなかった。
  除外記録にも残らないため、呼び直しても同じ候補で起動した。
  対策: Codexの失敗の`message`がJSONの`error`オブジェクトを持ち`param`が`model`である場合を、除外理由`modelRejected`の可用性失敗として判定へ加える

- 2026年10月2日: pyfltrの処理で、agents_serverのMCPサーバーが実行中のCodexの委譲session 2件を抱えたまま正常終了した。
  登録簿は`running`のまま残った。
  直接原因: マネージャーの停止処理がbackendの停止（Codex App Serverの終了、Claude・Antigravityの所有taskの取消）を呼んだ後、保持中のsessionを終了へ遷移させず、登録簿へ終了を公開する唯一の手段である状態の更新通知を呼ばなかった。
  対策: マネージャーの停止処理で、backendの停止で終わった未終了のturnを全engine共通に`interrupted`へ遷移させ、登録簿と結果ファイルへ公開する。
  再発（2026年10月11日）: 委譲元のClaude Codeの終了後、Claudeの委譲先の登録簿の記録が`running`のまま残った。
  所有するagents_serverの2プロセスは、停止処理（終了の公開と状態ファイルの削除）を経ずに終了していた。
  直接原因は10月2日の対策が回収の手段を正常な停止での終了公開とCodexの`thread/read`による引き継ぎに限り、登録簿の記録がturnを実行するプロセスの識別を持たなかったことである。
  engine `claude`は引き継ぎの対象外だった。
  対策として、登録簿の記録へ所有するagents_serverと委譲先CLIのプロセスの識別（pidと起動時刻）を保存する。
  `session_registry.resolve`は記録された全プロセスの不在を`interrupted`の終了と解決し、登録簿を書き換えない。
  再発（2026年10月25日）: plugin更新で委譲元のagents_serverが新しいプロセスへ替わった後、旧サーバーの下で作業を続けていたCodexの委譲先を、新しいサーバーの`show`が`interrupted`と公開した。
  直接原因の1つ目は、引き継ぎのCodex分岐が記録プロセスの実在を見ず、生存の印付き状態ファイルの不在と専用App Serverの`thread/read`の終了表示だけで終了を公開したことである。
  対策として、終了と所有の判定を記録プロセスの実在を一次の根拠とする`session_registry.running_ownership`へ集約し、委譲先CLIが実在する記録を所有サーバーを失った実行中として案内付きの`running`で返す。
  `kill`は記録と一致する委譲先CLIだけを止めて`interrupted`を公開する

- 2026年10月4日: agents_serverのClaude委譲先が`atk agents wait`を背景タスクで起動して待機表明でturnを終えた直後に、その背景タスクが終わった。
  agents_serverは待機表明を終了時の結果として公開し、委譲元は止まったように見えた。
  直接原因: 結果を保留するかを、agents_serverが組み立てる背景タスクと孫sessionの追跡集合だけで判定していた。
  対策: CLIが`session_state_changed`で報告するturn状態を受け取り、最新の報告が`idle`になるまで結果を保留する。
  シェルの背景タスクの稼働中も`idle`が届くため追跡集合も判定に残し、状態を報告しないCLIでは従来の判定へ戻って警告をログへ記録する

- 2026年10月6日: agents_serverのClaude委譲先がschedule起動のCIを待つバックグラウンドタスク（Bashの`timeout`は3,800,000ミリ秒）を残して待機表明でturnを終えた。
  直接原因: 保留の打ち切り期限が結果保持期限と同じ1800秒で、待機対象であるBashの背景実行の上限（環境変数を設定しない場合は7,200,000ミリ秒）と結び付いていなかった。
  対策: 期限を背景実行の上限に余裕を加えた値として上限の定数から導く。
  確定時に残るバックグラウンドタスクと孫sessionの記録を共通処理へ集約し、期限到来、ストリーム終端、保留中の`kill`と`send_message`のどの契機で確定しても`error`の`unfinishedBackgroundTasks`と`unobservedSessions`へ示す

- 2026年10月16日: `agent-toolkit:atk-mode`の作業`process-wi`の処理回で、終了済みのCodex委譲を5件`stop`した後も、レーンの専用worktreeをcwdとする`pyfltr mcp`が同じPIDのまま残った。
  直接原因: Codex backendの`release_session`は複数の委譲sessionが共有するApp Serverへ`thread/unsubscribe`を送り、その応答を受けて戻っていた。
  対策: Codex backendがApp Serverを委譲sessionごとに起動して所有し、`release_session`は所有するApp Serverと子孫の終了をOS上で確認してから戻る（`db9d2f825`）。
  終了を確認できない対象が残れば再試行できる失敗を返し、`stop`は解放済みと記録しない

- 2026年10月31日: 自身が起動した委譲先のcwd記録が欠けるとshow照会から回復できず、追送・中断操作が遮断された。
  MCP失敗応答が委譲先状態の更新へ渡り、stop拒否後の未観測記録を失う経路もあった。
  直接原因: showの稼働子一覧だけをcwd記録元とし、対象自身の起動元を照合していなかった。
  観測操作の共通境界でisError・ToolErrorを除外していなかった。
  対策: show対象ID、絶対cwd、永続登録の`launcher_session_id`、start時に保存した`launcher_agent_id`を照合する。
  所有不明・他者・ID不一致・不正cwdは遮断を維持する。
  全観測操作の失敗を状態・cwd・alias・待機登録・終了ログの更新前に除外し、終了session自身のerrorは区別する

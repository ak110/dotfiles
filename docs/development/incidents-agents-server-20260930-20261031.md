# 障害・欠陥の記録: agents_serverと委譲基盤の不全（2026年9月30日〜2026年10月31日）

[agents_serverと委譲基盤の不全](incidents-agents-server.md#agents_serverと委譲基盤の不全)の事例を発生時期別に収録する。

- 2026年9月30日: 工程別設定`codex:sol/medium,claude:opus[1m]/medium`の`start`が、`sol`系列の最新版として解決した`gpt-6.1-sol`で起動し、接続先がそのモデルを提供しないため最初のturnが失敗した。
  候補は第2候補のclaudeへ切り替わらず、呼び出し元が`model_type`を手で指定して起動し直した。
  失敗は`codexErrorInfo: other`で届き、`message`はJSON文字列で`error`オブジェクトが`code: invalid_parameter_value`と`param: model`を持っていた。
  直接原因: 可用性失敗の判定が`codexErrorInfo`の利用枠超過・流量制限・過負荷の区分とHTTPステータスだけを判定材料としており、モデルIDの不受理はどちらにも当たらなかった。
  除外記録にも残らないため、呼び直しても同じ候補で起動した。
  対策: Codexの失敗の`message`がJSONの`error`オブジェクトを持ち`param`が`model`である場合を、除外理由`modelRejected`の可用性失敗として判定へ加える

- 2026年10月2日: pyfltrの処理で、agents_serverのMCPサーバーが実行中のCodexの委譲session 2件を抱えたまま正常終了した。
  登録簿は`running`のまま残った。
  委譲先のturnは終わっていた（`thread/read`で最後のturnが`interrupted`）のに、再起動後の`show`・`send_message`は実行中の可能性として拒否し、起動元rootの`atk agents wait`は待機上限（270秒）ごとの終了を約80分繰り返した。
  直接原因: マネージャーの停止処理がbackendの停止（Codex App Serverの終了、Claude・Antigravityの所有taskの取消）を呼んだ後、保持中のsessionを終了へ遷移させず、登録簿へ終了を公開する唯一の手段である状態の更新通知を呼ばなかった。
  再起動後の復元は、終了の公開の欠落を前プロセスの実行継続と区別できず、所有者のいない記録を回収する手段を持たなかった。
  既存テストは実行中sessionを持つマネージャーの停止後の登録簿を確かめていなかった。
  対策: マネージャーの停止処理で、backendの停止で終わった未終了のturnを全engine共通に`interrupted`へ遷移させ、登録簿と結果ファイルへ公開する。
  既に`running`のまま残ったCodexの記録は、生存の印が有効な状態ファイルがそのsessionを載せておらず、`thread/read`が元turnの終了を示す場合だけ、再起動後の`show`・`send_message`の照会で終了として引き継ぐ。
  元turnと比べられるよう登録簿へCodexのturn識別子を加えた。
  経過時間による推定と待機側による登録簿の書き換えは行わない。
  停止後の再開と、引き継ぎの成功・拒否の各条件をテストで確かめる
  再発（2026年10月11日）: 委譲元のClaude Codeの終了後、Claudeの委譲先の登録簿の記録が`running`のまま残った。
  所有するagents_serverの2プロセスは、停止処理（終了の公開と状態ファイルの削除）を経ずに終了していた。
  turnを実行するClaude CLIも存在しなかったが、MCPの`show`は別プロセスの実行中と返し、`atk agents show`は`unknown session`を返した。
  引き継いだCodexのメインはOSのプロセス一覧を別に調べて終了を判断した。
  直接原因は10月2日の対策が回収の手段を正常な停止での終了公開とCodexの`thread/read`による引き継ぎに限り、登録簿の記録がturnを実行するプロセスの識別を持たなかったことである。
  engine `claude`は引き継ぎの対象外だった。
  停止処理を経ずに終了した契機（ホストの終了処理による強制終了か、Ctrl+Cの連打か）は、ログの回転のため判別していない。
  対策として、登録簿の記録へ所有するagents_serverと委譲先CLIのプロセスの識別（pidと起動時刻）を保存する。
  `session_registry.resolve`は記録された全プロセスの不在を`interrupted`の終了と解決し、登録簿を書き換えない。
  MCPの`show`・`send_message`、`atk agents show`・`wait`、自動再開、`atk lane`は同じ解決を使い、照会時の引き継ぎはこの終了をengineを問わず公開する。
  識別を持たない既存の記録は従来どおり実行中とする。
  実プロセスを起動・終了させた記録で各照会をテストで確かめる
  再発（2026年10月25日）: plugin更新で委譲元のagents_serverが新しいプロセスへ替わった後、旧サーバーの下で作業を続けていたCodexの委譲先を、新しいサーバーの`show`が`interrupted`と公開した。
  続く`send_message`は新しいApp Serverの`thread/resume`が`already has an active writer`で拒否されて`failed`を公開し、委譲先の上り通知も旧ルートに残って届かなかった。
  直接原因の1つ目は、引き継ぎのCodex分岐が記録プロセスの実在を見ず、生存の印付き状態ファイルの不在と専用App Serverの`thread/read`の終了表示だけで終了を公開したことである。
  2つ目は通知の回収が自身のルートだけを読んだことである。
  対策として、終了と所有の判定を記録プロセスの実在を一次の根拠とする`session_registry.running_ownership`へ集約し、委譲先CLIが実在する記録を所有サーバーを失った実行中として案内付きの`running`で返す。
  `kill`は記録と一致する委譲先CLIだけを止めて`interrupted`を公開する。
  登録簿から解決したsessionの通知と終了時の結果は所有者のいない他のルートからも回収する。
  停止時の中断公開、所有しない`stop`、起動時の同名状態ファイルの削除、登録簿の7日掃引とprocess-loopの記録も同じ判定へ合わせ、MCPツールとCLIの呼び出し口から通す受入テストで確かめる

- 2026年10月3日: agents_serverの内部保存項目がCLI一覧や結果・通知の公開応答へ混入し、活動情報と導出できる集計値を重ねて返していた。
  直接原因: 内部辞書の公開と除外リストを用い、消費側が必要とする項目集合を公開境界で定めていなかった。
  CLI一覧の起動文を減らした変更は、2026年9月16日「fix: atkのサブコマンドを保存と再読なしで1回呼べるようにする」、`b58cbc718`である。
  結果保存を共通化した変更は、2026年9月8日「fix: agents_serverの共有状態判定を集約する」、`c42c6cada`である。
  通知回収を共通化した変更は、2026年9月7日「fix(agents-server): agents-waitで通知を回収してturn指定を不要にする」、`5af740b9c`である。
  これらの変更も内部保持に必要な値と公開に必要な値を分けていなかった。
  値の存在を期待するテストは、用途外の内部項目が応答へ流れ込むことを検出しなかった。
  対策: 活動、結果、通知の応答を用途ごとの項目へ射影する（2026年10月3日「fix(agent-toolkit): agentsの公開応答を用途に限定し所有rootへ通知を接続する」、`b99d9fd4f`）。
  CLI一覧のラベルと開始時刻、詳細表示の依頼と実行条件、人間向けツリーの所有関係、結果回収の本文と所有者確認、通知回収の送信順の本文は保持する。
  内部の保存・所有者確認・耐久退避・整列を維持し、旧保存物にも回収時の射影を適用する。
  内部に仮の追加項目を持つ検体で公開への流入を検証する

- 2026年10月3日: ownerの無いMCPプロセスが生成したrootとCodex backendの環境解決が分かれ、委譲先の途中通知を同じrootへ保存できなかった（`b99d9fd4f`）。
  直接原因: backendがManagerの所有rootを入力に使わず、環境から独立に所有者を解決していた。
  対策: ManagerのStatusFileWriterのrootを全backendの新規・再開環境、Codexのthread設定とhost索引へ配送する。
  環境ownerの有無、異なるManagerと再開の条件で通知保存・回収を接続する。
  全root走査、新しい状態台帳、Antigravity固有の通知元解決は追加しない

- 2026年10月4日: agents_serverのClaude委譲先が`atk agents wait`を背景タスクで起動して待機表明でturnを終えた直後に、その背景タスクが終わった。
  agents_serverは待機表明を終了時の結果として公開し、委譲元は止まったように見えた。
  委譲先は完了通知で再開して作業を続けたが、その結果は委譲元へ届かなかった。
  直接原因: 結果を保留するかを、agents_serverが組み立てる背景タスクと孫sessionの追跡集合だけで判定していた。
  Stop hookの実行中（観測では最終応答から約430ms）に背景タスクが終わると、完了通知が`ResultMessage`より先に届いて追跡集合が空になり、`ResultMessage`の時点で待機表明を確定した。
  Claude Code CLIはturnの終了前にキューへ入った完了通知でも次のturnを開始するが、agents_serverは確定後のメッセージを読まなかった。
  待機表明の公開を直した2026年9月26日「fix(agents_server): 委譲先が自動再開後に返した最終結果を呼び出し元が受け取れるようにする」、`057ce566c`は自動再開turnの分岐を局所的に直し、再発防止策を範囲外としたため、到着順の前提は見直されなかった。
  単体テストとライブテストも、完了通知が`ResultMessage`より前に届く順序を含まなかった。
  対策: CLIが`session_state_changed`で報告するturn状態を受け取り、最新の報告が`idle`になるまで結果を保留する。
  シェルの背景タスクの稼働中も`idle`が届くため追跡集合も判定に残し、状態を報告しないCLIでは従来の判定へ戻って警告をログへ記録する。
  テストの偽クライアントへ実CLIと同じturn状態の報告の順序を写し、到着順だけを変えた回帰テストを置いた。
  委譲先への指示とStop hookは変えていない

- 2026年10月6日: agents_serverのClaude委譲先がschedule起動のCIを待つバックグラウンドタスク（Bashの`timeout`は3,800,000ミリ秒）を残して待機表明でturnを終えた。
  1802秒後に委譲元の`atk agents wait`が、待機表明だけを本文に持つ`status: completed`、`error`なしの結果を返した。
  バックグラウンドタスクはその約7分後に完了し、委譲元は同じsessionへ続きを依頼し直して最終の返却を得た。
  直接原因: 保留の打ち切り期限が結果保持期限と同じ1800秒で、待機対象であるBashの背景実行の上限（環境変数を設定しない場合は7,200,000ミリ秒）と結び付いていなかった。
  保留結果を確定する共通処理は確定時に残る待機対象を記録せず、記録は呼び出し元ごとに孫sessionだけを対象としていたため、稼働中のバックグラウンドタスクを残した確定が正常な完了として公開された。
  期限到来の契約テストも、稼働中のバックグラウンドタスクを残した`error`なしの確定を期待値として固定していた。
  対策: 期限を背景実行の上限に余裕を加えた値として上限の定数から導く。
  確定時に残るバックグラウンドタスクと孫sessionの記録を共通処理へ集約し、期限到来、ストリーム終端、保留中の`kill`と`send_message`のどの契機で確定しても`error`の`unfinishedBackgroundTasks`と`unobservedSessions`へ示す。
  保留の確定は`heldResultFinalized`で示す。
  `atk agents wait`の`終了行:`へ`unfinished_waits`を加え、委譲元の手順へその結果を再開turnの結果として扱わないことを書いた。
  各契機の記録と期限の関係をテストで確かめる

- 2026年10月8日: Codex App Serverのreader障害と対象を特定できないserver requestの処理が、同じmanagerのClaude・Antigravity sessionもfailedへ変更する実装になっていた。
  直接原因: backend間で共有するsession一覧に対し、全件処理とthread・turn検索がengineを限定していなかった。
  対策: Codex sessionだけを返す共通の検索境界を使い、Backendの契約と共有状態表へ更新主体を明記する。
  自動再開の保留中Claudeを含む混在一覧で、他engineの状態・エラー・保留・結果公開状態が変わらないことを検証する

- 2026年10月16日: `agent-toolkit:process-wi`の処理回で、終了済みのCodex委譲を5件`stop`した後も、レーンの専用worktreeをcwdとする`pyfltr mcp`が同じPIDのまま残った。
  同じ処理回では`atk lane delete`と`atk agents-exit-session`がそのプロセスをレーンの使用中と判定し、回収を保留した。
  直接原因: Codex backendの`release_session`は複数の委譲sessionが共有するApp Serverへ`thread/unsubscribe`を送り、その応答を受けて戻っていた。
  App Serverは購読解除の後も猶予の間threadのstdio MCPを生存させるため、`stop`が解放済みと記録した時点でもMCPがレーンを使い続けた。
  Codex CLI 0.163.0で、購読解除後もthreadが`thread/loaded/list`に残りMCPも生存すること、起動元のApp Serverを終了するとMCPも終了することを確かめている。
  背景原因: 委譲sessionの解放完了を、共有する実行系への非同期要求の受理で代用し、委譲の解放単位とプロセスの所有単位が一致していなかった。
  テストの偽クライアントは購読解除の応答だけを返してMCPの終了を表現せず、要求の送信成功を解放の検証としていたため検出できなかった。
  対策: Codex backendがApp Serverを委譲sessionごとに起動して所有し、`release_session`は所有するApp Serverと子孫の終了をOS上で確認してから戻る（`db9d2f825`）。
  終了を確認できない対象が残れば再試行できる失敗を返し、`stop`は解放済みと記録しない。
  保持期限の失効、起動候補の断念、managerの停止も同じ解放境界を通す。
  thread開始でstdio MCPを起動し購読解除では終了させない実Codexの挙動を模擬App Serverの実プロセスで再現し、`stop`後のプロセス終了とレーン削除、他sessionの継続と再開、回収失敗の再試行を結合テストで確かめる

- 2026年10月31日: 自身が起動した委譲先のcwd記録が欠けるとshow照会から回復できず、追送・中断操作が遮断された。
  MCP失敗応答が委譲先状態の更新へ渡り、stop拒否後の未観測記録を失う経路もあった。
  直接原因: showの稼働子一覧だけをcwd記録元とし、対象自身の起動元を照合していなかった。
  観測操作の共通境界でisError・ToolErrorを除外していなかった。
  対策: show対象ID、絶対cwd、永続登録の`launcher_session_id`、start時に保存した`launcher_agent_id`を照合する。
  所有不明・他者・ID不一致・不正cwdは遮断を維持する。
  全観測操作の失敗を状態・cwd・alias・待機登録・終了ログの更新前に除外し、終了session自身のerrorは区別する。
  公開PostToolUseの修正前44ケースでは24件失敗・20件成功、同じ44ケースの修正後は全件成功を確認した

# 所有者不在の復旧と子threadの設計記録

[設計記録の索引](design.md)から参照する、所有者不在の復旧と子threadの記録である。

終了と所有の判定は記録プロセスの実在を一次の根拠とし、`session_registry.resolve`と`session_registry.running_ownership`へ集約する。
生存の印が有効な状態ファイルがsessionを載せている`running`の記録は所有下（`owned`）とする。
載せておらず委譲先CLIが実在する記録は所有サーバーを失った実行中（`owner_lost`）、どちらでもない記録は所有者のいない記録（`orphaned`）とする。
登録簿の`processes`は先頭を所有サーバー、以降を委譲先CLIとし、所有サーバーのルート（`owner_root_session_id`）も記録する。
所有サーバーの起動単位が替わっても、応答しない旧起動単位や委譲先CLIが生存し続ける場合があり、状態ファイルの生存の印や外部照会だけで終了・所有不在を決めると作業中の委譲先を中断と公開するためである。
所有サーバーを失った実行中のsessionは終了を公開せず実行中として扱う。
MCPの`show`と`atk agents show`は`status`を`running`とし、`ownership`と記録プロセス（`recorded_processes`）、記録時のルートを返す。
旧ルートに残る同じsessionの上り通知の件数（`stranded_notice_count`）と、終了を待つか`kill`の後に`send_message`する`next_action`も返す。
`send_message`は新しいbackendで再開せず、同じ案内のエラーを返す。
旧委譲先CLIが会話の書込主体を保持しており、Codexの`thread/resume`は`already has an active writer`で拒否されるためである。
`atk agents wait`はそのsessionを実行中として待ち続ける。
MCPの`kill`は登録簿のpidと起動時刻が一致する委譲先CLIだけを子孫ごと停止し、停止を確かめてから`interrupted`を公開する。
所有サーバーのプロセスは他のsessionの資源を持ち得るため止めない。
停止後の`send_message`は通常の復元で同じ会話を再開する。
新しい公開状態は追加せず、既存の`running`へ診断項目を加える。
同じ根本原因を持つ処理も記録プロセスの実在と矛盾する終了・解放・削除を公開しない。
停止時の中断公開は、backendの停止後も委譲先CLIが残るturnへ`interrupted`を公開せず実行中のまま残す。
自プロセスが所有しない記録で記録プロセスが実在する場合、`stop`は解放済みへ置き換えず結果ファイルも削除しない。
起動時の状態ファイルの削除は、同名のファイルの生存の印が有効で、書いたプロセス（`writer_process`）が別プロセスとして実在する場合に行わない。
このとき自身は`<書込主体>-p<pid>.json`へ書く。
登録簿の7日掃引は、終了していない記録で記録プロセスが実在するものを残す。
process-loopのオーケストレーターの記録は、環境へ渡したsession識別子が一致する`claude`の子プロセスを委譲先CLIとして加える。
委譲先の上り通知と終了時の結果は委譲先の起動時のルートへ届くため、Codexを親とするMCPサーバーのように起動単位ごとにルートが替わると、新しいサーバーは届いた先を読まない。
登録簿から解決したsession（自プロセスが起動していないsession）の通知と終了時の結果は、`atk agents wait`とMCPの`show`・`send_message`・`kill`が自身のルートに加えて他のルートからも回収する。
対象は生存の印が有効な書込主体のいないルートである（`_agents_server/owner_lost.py`）。
却下した代替案は、引き継ぎそのものを除く案と、通知の宛先を委譲元の現在のルートへ書き換える案である。
前者は強制終了で全プロセスが消えた記録を回収できなくなり、後者は委譲先の環境を起動後に変更できず、孫の通知にも作用しない。
旧サーバーが交代後に終了しなかった理由は記録プロセスの生存を判定の入力にすることで結果に影響しないため、扱わない。
知識境界として、委譲先が返す文面と待機表明の可否の規定は`agent-toolkit/share/rules/rules-subagent.md`と`agent-toolkit/share/agents-server-auto-resume.md`が持つ。
本節は待機表明の判定入力を委譲手段ごとに生成する方法だけを扱う。
待機表明を許すかは、能力を実行するサーバーが`share/agents-server-*.md`の指示で伝える。
委譲元の起動入力で指定する方式は採らない。
委譲元は起動の時点で採用されるbackendを確定できず、指定させると実際の能力と一致しない場合が生じるためである（2026年10月1日、ユーザーの指摘「起動時に余計な指示」）。
却下した代替案は、全委譲手段へ共通の無条件の待機終了規範を置く案と、待機表明の文面を委譲元が判別して継続要求を送る案である。
前者は自動再開を持たない委譲手段で返却値を回収できない終了をそのまま残し、後者は`agent-toolkit:delegation`の`references/waiting-and-monitoring.md`が禁じる文面判別に当たるため採用しない。
委譲プロンプトへ委譲手段の能力を書かせる案も、`agents_server`はengineを起動時に解決するため委譲元が委譲プロンプトの作成時点で確定できず採用しない。

Stopフックによる継続の強制は採用しない。
バックグラウンドタスクの完了は委譲先が同じturn内の追加行動で解消できず、backendが終了とCLIのturn状態を観測して自動再開後の結果を公開するためである。
Claudeの軽量起動へもユーザー設定と明示ロードするプラグインを渡す。
停止と継続の判定は、フックの不在を条件とせず、担当区分と自動再開の契約に合わせる。
委譲元へバックグラウンドタスクの判別値を返す方式も採用しない。
再開の要否と時機を委譲元へ委ねると、`send_message`を送る追加の分岐が必要になり、同じsessionが自動的に進める処理を公開契約へ露出させるためである。

Codex backendは組み込みの委譲による子threadも保留の対象とする。
App Serverの`subAgentActivity`の`started`で稼働集合へ加え、同じ`agentThreadId`の`completed`・`interrupted`で終了集合へ移す。
購読した子threadの`turn/completed`も終了として観測する。
子threadの通知は親のstatusや返却本文へ混ぜない。
同じitemは開始・完了・turnの完了で再掲されるため、送信元thread・turn、item IDと活動種別で一度だけ反映する。
保留対象の有無は`resume_waits.has_pending_auto_resume_targets`へ集約し、全対象の終了後に既存の監視が同じsessionへ一度だけ継続を送る。
再開後に新しい子を起動した場合は新しい完了待ちとして数える。
期限到来と接続喪失で保留を確定するときは、未終了のthread IDも既存の`error.unobservedSessions`と`heldResultFinalized`へ記録する。

Codexの自分宛て送信は`subAgentActivity`の`interacted`と、委譲先自身を指す`agentThreadId`で検出する。
委譲先自身のthreadで発生した項目だけを数え、子から親への通常の配送は送信元threadで区別する。
この送信を含むturnの結果は保留し、同じsessionへ「送った本文は委譲元へ届いていない。全文を最終応答へ書いて返す」と一度だけ継続を送る。
子が稼働中なら全対象の終了まで待ち、子の結果回収と本文の修復を一度の継続へまとめる。
修復の消費済み印はsessionの寿命で保持し、修復後のturnで同じ送信を検出してもその結果を返す。
起動時の`mode`の値にかかわらず同じ項目処理を使う。
子threadへの`interacted`は通常の指示として扱う。
Codexが委譲先へ`/root`の役割と`send_message`を案内する一方、agents_serverの返却方法は最終応答本文であることが、この修復を置く理由である。
`subAgentActivity`は送信本文を持たないため、検出した項目から本文を合成する案は採用しない。
根拠となる公開契約はCodex App Serverの`ThreadItem`・`SubAgentActivityKind`・`ItemCompletedNotification`・`TurnCompletedNotification`である。

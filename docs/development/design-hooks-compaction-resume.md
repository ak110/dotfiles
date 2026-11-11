# 会話圧縮と利用上限後の再開予約の設計記録

[設計記録の索引](design.md)から参照する、会話圧縮と利用上限後の再開予約の記録である。

## Function hooks moduleの個別設計

### メインの会話圧縮予約

`compact_conversation`はClaude Codeのメインへ引数なしの会話圧縮予約を提供する。
`register.ts`がメイン専用の登録と委譲先の判定を「send_to_userツール」と共有し、同じ判定を自動経路にも渡す。
Codexにはこのmodを配らない。
`compact_conversation.ts`は受付と完了を分け、turn間の単発タイマーから圧縮を実行する。
surfaceを持つ会話では公開APIの`session.compact()`、headlessでは公開`command.run`の`compact`を使う。
turn実行中の予約はturn完了へ持ち越す。
成功・skip・失敗を分け、skipと失敗は予約を解除し、追加LLM turnを発生させないtoastで通知する。
現在会話の背景Bashタスクを検出し、使用量が400,000トークンと文脈上限の40%の大きい方以上なら同じ予約へ自動接続する。
予約の判定はturn実行中も行い、実行前に完了通知が到着しても、受け付けた予約をturn間まで保持する。
上限の小さいモデルでも400,000トークン未満では自動圧縮しない。
`atk agents wait`の所有と待機対象は、`wait-compaction-context`が既存の状態と待機登録から確認する。
起動区分による除外は行わず、CI監視などほかの背景Bashも対象とする。
見送り条件と予約は`ui.log`のdebug出力と、会話別の`agent-toolkit-function-hooks/compaction-<会話ID>.txt`へ記録し、使用量不明・閾値未満・所有未確認・予約済み・待機終了・委譲先などを区別する。
背景実行を要求したBashの戻り値に待機IDが無いか不正な場合も、待機を検出できなかった理由として残し、hookが動かなかった場合と区別する。
前景のBashは待機ではないため記録しない。
process-loopは`--debug=hooks`で起動し、modの診断はそのdebugログに現れないため、状態ファイルを読み先とする。
状態ファイルは直近20件の判定と入力（使用トークン数と上限、予約中・turn実行中）を持ち、寿命は同じディレクトリの他のファイルに従う。
同じ待機の自動予約は消費済み状態で1回に限り、未完了の予約は重ねない。
新しい背景待機では、直前の圧縮の成功・見送り・失敗によらず使用量と所有条件を再評価する。
使用量の低下を観測しなくても次の待機へ進めるよう、待機単位の消費と未完了予約だけで重複を抑止する。
完了通知で待機を除き、turnと単発タイマーで判定する。
永続予約・継続ポーリング・追加のモデル呼出しによる判定は設けない。
圧縮の実装と結果はホスト、予約と自動発動条件はmodが持つ。
状態保存と通知後の復元はdelegationのwaiting-and-monitoring.mdが所有する。
2026年10月9日のcommand.run方式の観測は監査記録へ保持し、現在の公開APIとturn間の契約は同月23日の記録へ分ける。
終了要求へ混ぜる案は、再予約できる圧縮とセッションを終える操作の寿命が異なるため採らない。
対話UIと非対話の結果、ホスト版と再検証手段はaudit-records.mdの会話圧縮予約の項に保持する。

公開`session.compact`の主会話を処理するときは、会話IDから一意に解決した原本ログの所在を返し、要約モデルの呼出しを省く。
原本の解決を別の同期台帳へ保存せず、既存のsession記録解決を使う。
未解決・例外・agentId付きでは標準の処理を保ち、precomputeは先行要約を見送る。
同じpluginが`session.compact()`を予約した経路では、自身の置換hookが再入除外されるため標準の要約生成になる。
headlessの`compact`コマンドはホスト側の圧縮として原本置換hookを適用する。
追加pluginで再入を回避する案は配布構成と導入負担を増やすため採らない。
SDKの委譲先はターン間の同APIが未対応なので、所有backendが公開`/compact`入力を配送する。
Codexの独立圧縮turnと合わせ、所有と結果保留の構造は[design-agents-server.md](design-agents-server-hold-state.md#結果の保留と自動再開)が扱う。
原本を使う復元は要約を生成するホストでも共通とし、目的・最新訂正・認可・未完了を発話順・主体・参照位置から確認する。
実機の適用範囲と再検証はaudit-records.md「agent-toolkit/hooks/compact_conversation.ts：原本参照圧縮：2026年11月2日」にある。

### 利用上限後のメインの再開予約

観測と再検証手段は`docs/development/audit-records-usage-limit.md`の「docs/development/design-hooks-compaction-resume.md：利用上限後のメインの再開予約：2026年10月14日」にある。

メインの利用上限では、StopFailureのコマンドhookが再開要求を保存し、Function hooksの`turn.complete`が既存の再開予約へ接続する。
Remote Control接続ではホストの自動継続が自動で始まらないため、起動形態に対応した再開の契機を確保する。
コマンドhookは`rate_limit`時にClaude configの`agent-toolkit-function-hooks/rate-limit-{session_id}.txt`へ`requested`を書く。
`usage_limit_resume.ts`は要求を`consumed`へ変え、解除予定後に同じ会話へ継続指示を送る予約をsessionごとに1件保持する。
予定時刻が無いか過ぎている場合は300秒後に再確認する。
解除予定後に別turnが始まっていれば重複投入せず、予約の表示は投入・取消時に除く。
`isDelegated`が真のsessionと`agentId`を持つturnは対象から外す。
1行目が`atk-auto`の開始タグであるpromptは、既存のUserPromptSubmitの機械注入判定へ接続する。
promptは再開情報を読んで最初の未完了工程から続け、完了・続行済み・ユーザー中止では終了することを伝える。
コマンドhookが要求の保存、modが再開予約、usage-limit手順が再開後の工程を所有する。
agents_serverは委譲先の解除待ちを所有する。
解除待ちの委譲先、背景waitと定期再確認のtaskは保持する。
猶予通知の実行中サブエージェントの説明は同じ会話のAgentを指し、独立したagents_serverの解除待ちへ当てはめない。
猶予時にモデルがcronを予約する案は、最初から拒否されたturnでは操作機会が無く、規範の想起も必要となるため採らない。
Remote Controlを無効にする案はユーザーの遠隔操作を失わせるため採らない。
組織設定で省略される`classic.*`への依存を外し、コマンドhookの要求を`turn.complete`へ渡す。
予約のタイマーはsession内にあり、module再読込で失われる。
ホスト挙動の典拠と未観測範囲は[audit-records.md](audit-records.md)の利用上限後の再開の項にある。

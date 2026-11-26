# 会話圧縮と利用上限後の再開予約の設計記録

[設計記録の索引](design.md)

## メインの会話圧縮予約

`compact_conversation`はClaude Codeのメインへ引数なしの会話圧縮予約を提供する。
既存agents_server MCPが予約を受け、設定PreToolUseとMCPの双方でメイン限定を確認する。
会話内modの確認応答を受けて受付を返し、同じmodがターン間で圧縮を実行する。
Codexにはこのmodを配らない。
turn実行中の予約はturn完了へ持ち越す。
自動圧縮の発動条件はホストの設定に従い、バックグラウンド待機からは予約しない。
状態保存と通知後の復元は`agent-toolkit:atk-mode/playbooks/delegation-waiting.md`が所有する。
2026年10月9日のcommand.run方式の観測は監査記録へ保持し、現在の公開APIとturn間の契約は同月23日の記録へ分ける。
終了要求へ混ぜる案は、再予約できる圧縮とセッションを終える操作の寿命が異なるため採らない。
対話UIと非対話の結果、ホスト版と再検証手段はaudit-records.mdの会話圧縮予約の項に保持する。

公開`session.compact`の主会話を処理するときは、会話IDから一意に解決した原本ログの所在を返し、要約モデルの呼び出しを省く。
原本の解決を別の同期台帳へ保存せず、既存のsession記録解決を使う。
未解決・例外・agentId付きでは標準の処理を保ち、precomputeは先行要約を見送る。
同じpluginが`session.compact()`を予約した経路では、自身の置換hookが再入除外されるため標準の要約生成になる。
headlessの`compact`コマンドはホスト側の圧縮として原本置換hookを適用する。
追加pluginで再入を回避する案は配布構成と導入負担を増やすため採らない。
原本を使う復元は要約を生成するホストでも共通とし、目的・最新訂正・認可・未完了を発話順・主体・参照位置から確認する。
組織ガードで`session.compact`が省略される場合も登録は残し、設定PreCompactから原本復元を優先する短い要約を依頼する。
PreCompactは要約APIそのものを省略する契約ではない。
実機の適用範囲と再検証はaudit-records.md「agent-toolkit/hooks/compact_conversation.ts：原本参照圧縮：2026年11月2日」にある。

## 再起動と同一会話への反映

ユーザーが入力した`/restart`では、設定hookが実測したバックグラウンド待機の共有一覧から、既存の所有照会が子担当を返す`atk agents wait`だけを停止する。
一般のバックグラウンド処理は停止しない。
停止後は会話別の再開印を保存し、次のSessionStartが送信前に消費して自動続行を1回だけ予約する。
modからの`/restart`制限を回避する経路は作らない。

`apply_runtime_changes`はメインから対象を予約し、turn完了後に公開のMCP再接続コマンドとプラグイン再読み込みを実行する。
再読み込み前に消費状態と個別応答を会話状態へ保存し、操作の重複実行を防ぐ。
途中でmodが再読み込みされた場合は、応答済みと未確認を区別して継続を通知する。
受付、コマンド応答、反映後の動作確認はそれぞれ独立した結果として扱う。

## 利用上限後のメインの再開予約

メインの利用上限では、StopFailureのコマンドhookが再開要求を保存し、Function hooksの`turn.complete`が既存の再開予約へ接続する。
Remote Control接続ではホストの自動継続が自動で始まらないため、起動形態に対応した再開の契機を確保する。
`turn.complete`の時点で`five_hour`か`seven_day`の使用率が100%以上の場合も、要求の保存が無くても同じ予約へ進む。
`usage_limit_resume.ts`は要求を`consumed`へ変え、解除予定後に同じ会話へ継続指示を送る予約をsessionごとに1件保持する。
解除予定後に別turnが始まっていれば重複投入せず、予約の表示は投入・取消時に除く。
`isDelegated`が真のsessionと`agentId`を持つturnは対象から外す。
コマンドhookが要求の保存、modが再開予約、usage-limit手順が再開後の工程を所有する。
agents_serverは委譲先の解除待ちを所有する。
解除待ちの委譲先、背景waitと定期再確認のtaskは保持する。
猶予通知の実行中サブエージェントの説明は同じ会話のAgentを指し、独立したagents_serverの解除待ちへ当てはめない。
猶予時にモデルがcronを予約する案は、最初から拒否されたturnでは操作機会が無く、規範の想起も必要となるため採らない。
Remote Controlを無効にする案はユーザーの遠隔操作を失わせるため採らない。
組織設定で省略される`classic.*`への依存を外し、コマンドhookの要求を`turn.complete`へ渡す。
予約のタイマーはsession内にあり、module再読み込みで失われる。
ホスト挙動の典拠は[mods API](https://code.claude.com/docs/en/plugins/mods/api)と[reference](https://code.claude.com/docs/en/plugins/mods/reference)で、2026年10月14日にClaude Code 2.1.299で確かめた。
再検証ではStopFailureの`rate_limit`からメインだけが再開要求を作ることを`agent-toolkit/agent_toolkit/_hooks/stopfailure_notifier_test.py`で確かめる。
要求の消費と予約は`agent-toolkit/hooks/usage_limit_resume.test.ts`で偽の使用率・時計・turnを与え、猶予後のturn完了、予定時刻不明、既再開、委譲先除外と表示回収を分けて確かめる。
実上限時の`percentUsed`と、猶予後の打切り時のStopFailure発火は観測しておらず、予約の永続化は検証の範囲に含めない。
Remote Control接続での猶予通知と解除後のturnの対応付けは[audit-records-usage-limit.md](audit-records-usage-limit.md)「agent-toolkit/skills/atk-mode/references/usage-limit.md：利用上限の予告・猶予通知への対応：2026年10月14日」にある。

## ユーザーの要求と見直しの基準

メインの会話圧縮の予約はユーザーの要求であり、作業契約の復元配送と状態の統合は別の具体化を含む。
圧縮の予約が働かない場合は、ホストの標準の圧縮と、作業記録・原本からの復元を使う。
利用上限の解除後の再開の自動化はユーザーの要求で、mod予約とタイマーの選択はエージェントの具体化である。
旧再開方式には解除から約8分の遅れなどの報告があり、解除を待つ正当な時間とは区別する。
予約が働かない場合は、正規の解除を待って同じ会話を明示的に継続する。
予約を整理する場合は、同じ解除条件での所要と再開の成功で比べる。

## 圧縮後の要約が同期処理で打ち切られた事例

圧縮後の要約は、WI `20261123-012545-001.md` に記録された事例では内部の15秒上限に達し、手動では約45秒で成功した。

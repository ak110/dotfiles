# 監査記録: 会話圧縮

[監査記録の索引](audit-records.md)に収録対象と記録の書式を示す。

## agent-toolkit/hooks/compact_conversation.ts：会話圧縮の予約：2026年10月9日

Claude Code 2.1.295で、基準commit `4573be59230a11bdc9d717a1a1e03d994abf284f`に本機能の未commit差分を加えたプラグインを検証した。
`CLAUDE_CODE_ENABLE_FUNCTION_HOOKS=1`を設定し、`--plugin-dir`に作業ツリーのagent-toolkitを指定した。

対話UIでは、モデルが`compact_conversation`へ任意指示を渡した後に現在ターンを終えた。
PTYに予約の受付と、その後の`Conversation compacted`・`Compacted`表示が保存された。
人による`/compact`入力は無く、セッションは正常終了した。

非対話では`claude -p --verbose --output-format stream-json --include-hook-events`を使用した。
モデルがBashのバックグラウンドタスクで65秒待機を起動し、予約後にターンを終えた。
同じセッションでPreCompact・PostCompactを観測した。
その後にmanual compact境界とホストの圧縮成功結果が現れた。
待機終了のtask-notificationから同じ会話が再開し、圧縮指示に指定した応答`COMPACT_BACKGROUND_RESUMED`を返して正常終了した。

故障の試行は隔離プラグインの`command.run`呼び出し直前に一度だけ例外を注入した。
実ホスト自身のコマンド拒否を観測した試行とは区別する。
セッションへ失敗原因と再予約可能の通知が届いた。
モデルは再予約し、同じ会話でmanual compact境界と圧縮成功を観測して正常終了した。
未完了予約の重複排除と成功後の再予約は、ツール登録から時計のコールバックまでを動かす結合試験でも確認した。

以上はinstructionsが存在した当時の観測であり、任意指示と指定応答も当時の事実として保持する。
現行契約は引数なしの予約と注入された要約と既存記録からの再開である。

再検証では隔離ディレクトリで作業ツリーのプラグインを読み込み、対話と非対話の両方でモデルへ引数なしの予約を指示する。
圧縮のための事前保存は行わない。
受付応答、ターン終了、ホストの圧縮成功を順に記録する。
非対話はBashのバックグラウンドタスクと組み合わせ、圧縮後に注入された要約を起点として同じ工程を再開できるか確認する。
要約だけでは足りない情報は既存の担当別記録・保存出力・状態照会から補い、同じsessionと担当・監視対象が続行することも確かめる。
任意指示で応答文字列を渡さない。
故障と再予約は隔離した検証用プラグインだけへ例外を注入して試し、製品のプラグインを改変しない。

## agent-toolkit/hooks/compact_conversation.ts：会話圧縮の予約：2026年10月23日

Claude Code 2.1.309の`claude plugin test agent-toolkit`で、公開modの登録・時計・turn・背景タスク・圧縮APIを使う結合試験を実行した。
`session.compact()`はturn間に呼び、成功のmessages、見送りのskip、例外を別の結果として扱う。
5秒未満、利用率49%、状態未保存、委譲先とサブエージェントは自動圧縮しない。
50%の境界、5秒後、turn完了への持越し、完了通知による取消し、失敗後の解放と自動再試行の抑止、閾値未満を経た再開を確認した。
Python側の公開`wait-compaction-context`も、所有・mode・待機登録・保存時刻の12ケースで確認した。
modの全136件は成功した。
外部モデルによる実際の要約生成はこの試験へ含まない。
再検証は同じ公開testコマンドと`wait_compaction_context_test.py`を使い、実際の要約生成を調べる場合は同月9日の隔離セッション手順で、予約・ホストの結果・待機の再開を分けて観測する。

## agent-toolkit/hooks/compact_conversation.ts：原本参照圧縮：2026年11月2日

- 観測日: 2026年11月2日。
- 確認版: Claude Code 2.1.316、Claude Agent SDK 0.2.174。
  直接の`/compact`入力は`session.compact`の返却messagesで原本所在へ置換し、復元turnがReadで元の目的・訂正・認可・未完了を確認した。
  同pluginの`$.session.compact()`予約では、debugの`session.compact skipped: re-entry (the plugin's own code raised it; origin ...)`と`source=compact`のAPI要求を観測した。
  公開`SessionCompactArgs`はinstructionsのみを受け付ける。
- 再検証: 読取と予約ツールだけを持つ新規対話セッションへ、目的・色の訂正・認可・未完了を含む検証入力を与える。
  最初は直接`/compact`、別の新規セッションでは予約ツールを1回使いターンを終える。
  原本の圧縮境界、要約要求の有無、復元時のReadと回答を比較する。
  実際の作業セッションは再開しない。
  `claude plugin test agent-toolkit`で公開hook入力のmanual・auto・plugin・precomputeと、記録未解決・失敗時の標準処理を検証する。
  同plugin予約はテストハーネスの外からのplugin入力と区別する。

## agent-toolkit/hooks/compact_conversation.ts：会話圧縮の予約：2026年11月4日

- 観測日: 2026年11月4日。
- 確認版: Claude Code 2.1.318。
  背景Bashの結果は`result.backgroundTaskId`で識別できた。
  同一turnの背景待機中は`turn_running`の予約抑止が成立し、完了通知の後にturnが終了しても予約されなかった。
  headlessの直接`session.compact`は未対応のHooksErrorを返した。
  slash commandsが有効な同じ隔離会話で`command.run({command:"compact"})`を使うと、compact_boundaryと背景タスクの結果回収を観測した。
  試行では使用量の閾値を縮小し、所有照会と原本所在だけをfixtureへ差し替えたため、製品の400,000トークン・40%閾値と所有判定の実データでの成立はこの観測の対象外である。
- 再検証: 読取・Bashだけを使う新規headless会話に、製品の圧縮modを登録する。
  独立した検証用背景タスクを開始し、同一turnで結果を待つ。
  使用量・所有条件が成立した入力で、予約診断、turn完了、compact_boundary、同一会話の背景結果回収の順を確認する。
  surfaceを持つ会話は直接APIとskip判定を比較する。
  `claude plugin test agent-toolkit`で通知先着、閾値両境界、委譲先の除外、予約重複、skip・例外後の再予約を確認する。
  通常の作業会話へ検証用の圧縮を送らない。

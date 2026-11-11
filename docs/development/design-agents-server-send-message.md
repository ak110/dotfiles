# 継続要求の配送の設計記録

[設計記録の索引](design.md)から参照する、継続要求の配送の記録である。

## 公開ツールの契約

### `send_message`

停止後・保持期限後・所有主体消失後の再開では、要求ごとの`ResumePrompt.delivery`へ配送結果を確定し、`PendingResume`が応答まで保持する。
backendの開始成功は`reply_started`とし、開始不能と開始不確定はbackendが`record_delivery`でそれぞれ`reply_failed`と`reply_ambiguous`を記録する。
共通応答はこの要求の配送結果を読み、新turnの`SessionState`の終了状態から配送成否を推定しない。
このため応答前の正常完了と開始後の実行失敗も、開始済みの配送結果を保持する。
再開前の未回収結果は`previous_result`で配送し、新turnの結果は既存の待機で別に回収する。
追送履歴、観測待ち登録と子session追跡は同じ公開の`delivery`を消費し、既存の`steered`も含めた値の集合を保つ。

`send_message(session_id, prompt, timeout=270)`は実行中turnへ追加指示を送り、終了済みturnでは結果回収を前提にせず同じsessionでreplyを開始する。
`timeout`を省略すると270秒を使うため、固有のtimeout要件がなければ引数を省略する。
timeoutは継続要求の配送結果が確定するまでの待機上限であり、`turn_control_lock`の取得を含む操作全体を覆う一方、委譲先の応答生成の完了は待たない。
`0`以下を受理しないのは、この操作の戻り値が配送結果の確定だけで構成され、待たない場合に返せる情報が無いためである。
上限到達時は配送の成否が確定せず、`atk agents wait`で状態を確認する。
`codex_fast_mode`の実効値はCodexの新しいturnを開始するたびに読み、`thread/start`・`thread/resume`・`turn/start`へ`serviceTier`を明示する。
`true`は`priority`、`false`は`default`とし、Codex側の`config.toml`のfast設定を`false`でも継承しない。
値を省略すると設定と動作が一致しないためである。
送信完了時に最新turnの値を`fast_mode`へ保持し、状態ファイル・登録簿・再開状態と詳細表示へ同じ値を渡す。
実行中のturnは変更せず、次のturnから設定変更を反映する。

steer拒否時は`willRetry: true`の`error`通知だけでは待機を終えず、結果の確定・turn変更・client failure・timeoutを待つ。
結果が確定した場合に限り、同じsessionでreplyを開始する。
reply開始の確定失敗は`reply_failed`、turn/start応答喪失は`reply_ambiguous`として配送する。

`send_message`は起動後に工程別モデル設定の候補列が変わっても、起動時に確定したengine・model・effortで継続する。
保持済みのsessionを失った場合だけ`unknown session`を返し、委譲元は検収済み状態を渡して新規起動する。

継続要求と中断要求の応答は、対象sessionを所有する実行主体だけが解決する。
Claude backendは要求の受理と応答の解決を1つの継続要求チャネルへ集約し、所有主体が保持期限の到達、message streamの例外、明示的な取り消しのいずれで終了する場合も、チャネルの閉鎖で受理済みの要求を所有主体の終了として解決する。
閉鎖後の受理は待機させず直ちに拒否する。
Codex backendは同じ責務をJSON-RPCの応答待ちへ適用し、接続の終了とstdout readerの失敗で未解決の要求を解決する。
この終了処理と呼び出し側のtimeoutを併用し、既知の終了の仕方では配送結果を即座に確定し、未知の停止では有限時間で制御を戻す。
却下した代替案は、所有主体終了時の解決を設けず、呼び出し側のtimeoutだけで応答なしを打ち切る案である。
要求が配送されたかを呼び出し側が判別できず、再開の可否も決められないため採用しない。

継続不能の判定は、実際に継続手段を呼び出した結果だけを入力とする。
`send_message`のtimeoutは配送の成否が未確定であることだけを示すため、継続不能の根拠にしない。
timeout後は、送った継続要求の受理結果、またはその要求によって始まった新しいreplyを確認できた場合だけ、同じthreadで待機を継続する。
`wait`が返す`status=completed`だけを配送済みの根拠にしない。
送った継続要求に対応する観測を得られない場合は、同じ継続指示を再送せず、継続不能とも確定せず続行できない理由を委譲元へ返す。
`agents_server`で継続不能と確定するのは`send_message`が`unknown session`を返した場合だけである。
判断理由は`send_message`が`turn_control_lock`の取得待ちでtimeoutした後に元から実行中だったturnが終了しても
`wait`は`status=completed`を返すため、元のturnの終了を今回の継続要求の配送へ対応付けられない点にある。
正常replyが前回と同じ定型本文を返し得るため、送信前後の`agent_message`の文字列一致も配送の成否を証明しない。
受理後に応答だけ失われた継続指示を再送すると重複配送になるため、対応付く観測を得られない場合も再送しない。
サーバー側の重複排除・配送識別契約を追加する案は、本計画の範囲を超えるため採用しない。

# ユーザーへの本文送達の設計記録

[設計記録の索引](design.md)から参照する、ユーザーへの本文送達の記録である。

## Function hooks moduleの個別設計

### send_to_userツール

Claude Codeでは、同じ応答でツール呼び出しより前に置いた地の文の一部を、APIが要約（progress updateの要約）へ置き換えて返す。
原文は画面にもtranscriptにも残らず、メインは届いたとみなして作業を続ける。
根拠は`audit-records-claude-output.md`の「agent-toolkit/share/rules-main.claude-code.md：ツールの入出力：2026年10月6日」、事象は`incidents-validation.md`の2026年10月6日の項にある。

ツールの入力は要約されないため、途中報告と最後の回答の両方をツールの入力で運ぶ。
確認質問は既存の質問手段を使う。
Function hooks module（`agent-toolkit/hooks/send_to_user.tsx`、`register.ts`から登録）は次を行う。

- `session.start`で`$.tool.register`により`send_to_user`（入力は必須の文字列`message`だけ）を`isDeferred: false`で登録する。
  モデルは初回要求から`mcp__agent-toolkit__send_to_user`の名前で呼ぶ。
  遅延一覧にだけ現れる環境では、その完全名を`ToolSearch`の`select:`で指定する。
  委譲先のセッションには登録しない
- `register.ts`の`tool.check`でメインからのこのツールの呼び出しを許可する。
  途中の報告のたびに権限確認の画面が出ると作業が止まるためである。
  委譲先と`agentId`を持つサブエージェントからの呼び出しは共通の一覧で拒否する
- `register.ts`の`tool.call`は委譲先とサブエージェントからの呼び出しを拒否し、結果本文を委譲元へ返すため最終応答へ書くよう案内する。
  個別moduleはメインからの呼び出しの入力を描画と共通の`messageOf`で確かめ、文字列だけに短い成功応答を返す。
  不正入力は未表示として拒否し、文字列`message`による再呼出を案内する。
  空文字は文字列として受理する。
  inputSchemaだけでは実行時の入力を保証できないためである
- `ui.render`の`ToolUse`でこのツールの行を`message`の`Markdown`要素で描き、他のツールの行は後続へ渡して描き替えない
- `ui.render`の`ToolResult`では、このツールの成功結果を空の`Box`で描く。
  本文は`ToolUse`に表示済みなので、成功確認を重ねて表示しない。
  成功文字列との一致を条件とせず、他ツールとエラー結果は後続へ渡す。
  terminalとdesktopに同じ条件を適用し、モデルへ返す`tool.call`の確認応答は保つ

メインへ初回から登録する理由は、どの報告でも必要になる小さな1ツールの定義を初回から渡すだけで、ToolSearchでこのツールを選ばずに報告する事態を除けるためである。
短い登録名とモデル側の完全名は別の層の名前であり、再起動や別名の追加では呼出名の不一致を解消しない。
ツールの登録と表示を同じmoduleに置くため、moduleを読み込まないClaude Code（管理設定の`CLAUDE_CODE_ENABLE_FUNCTION_HOOKS=1`が無い環境）とCodexにはツールが現れない。
その環境のメインは`rules-main.claude-code.md`「ツールの入出力」に従い、届ける内容をターンを終える応答の本文へ書く。

このツールはユーザーの画面を持つメインの報告手段であり、`agents_server`などが起動した委譲先は提供の範囲から外す。
委譲先の応答は委譲元が受け取る結果本文となるため、委譲先が表示済みとの応答を受け取ると、報告する本文を終了の返却から省き、委譲元が受け取る結果から根拠が欠ける。
2026年10月10日には、`agents_server`の`explore`のClaude委譲先がこのツールを呼び、固定の成功応答を受け取った。
別の委譲先は全ケースの根拠をツールの入力へ置き、終了では要約だけを返した。
委譲先は`agent_toolkit/_common/delegated_session.py`の`is_delegated`と同じ条件で判定し、`register.ts`の`isDelegated`がその条件を持つ。
条件は`AGENT_TOOLKIT_DELEGATED_SESSION`が`1`か、`AGENT_TOOLKIT_OWNER_SESSION`が空でないことである。
`register.ts`は委譲先への登録を省く。
メイン用ツールの定義は`register.ts`の`MAIN_ONLY_TOOLS`へ集約し、対象を指定しない新しい定義も同じメイン用の登録へ入る。
`tool.check`・`tool.call`は対象のツール名をこの一覧の名前と比べて一致を確かめ、委譲セッションまたは`agentId`を持つサブエージェントからの呼び出しを、個別の処理へ渡す前に拒否する。
`send_to_user`の拒否は本文を最終応答で委譲元へ返すよう案内する。
個別moduleは公開範囲を判定せず、描画・入力確認・圧縮予約を所有する。
委譲先の登録処理を持つClaude Codeの起動区分（`explore`、`task`、`delegate`、`write`、`shell`）は全て同じ条件で除かれる。
module全体を委譲先で読み込まない案は、委譲先にも必要な終了制御（`session_exit.ts`）を失うため採らない。
Claude Code 2.1.292では、hooks.jsonの`modules`は1件だけを受け付け、同じイベントで条件を持たないhookは1回だけ登録できる。
`$`を渡せるのは同じファイルで宣言した関数に限られる（2.1.296でも、別ファイルからimportした関数へ`$`を渡すmoduleは読み込みを拒否された）。
このため`register.ts`が唯一のmoduleとして`session.start`を1つにまとめ、メイン用ツールの一覧を登録する。
描画と終了制御の登録は公開範囲の判断から分ける。
両ファイルが共有する値はツールの定義と`$`を受け取らない関数に限る。
委譲先の判定が`$.env.get`を呼ぶため、判定関数と、それを使う登録とhookを同じ`register.ts`へ置く。
transcriptから可視本文を読むStop判定、言語判定と振り返りの証拠は、`_common/transcript.py`の`visible_text_blocks`で`send_to_user`の`message`を本文に含める。
質問の意味と確認手段は`user-confirmation-and-report`で扱う。
振り返りの証拠抽出は送信を作業ツールの境界から除き、同じrecordでも本文より後に作業があるかで途中と最終を区別する。
詳細照会は送信本文を全文で返し、他のツール入力や結果を混入させない。
名前の前置部分はmoduleの登録が決めるため、本文抽出では末尾の`__send_to_user`で判定する。

却下した代替案は次のとおりである。

| 案 | 採らない理由 |
| --- | --- |
| `agents_server`へツールを置く | MCPサーバーは画面へ描く手段を持たない。moduleを読み込まない環境でも成功を返し、原文を描かないまま届いたことになる。2026年10月6日にユーザーがmoduleへ置く選択肢を選んだ |
| 本体の組込み`SendUserMessage`を`--brief`か`CLAUDE_CODE_PEWTER_OWL_TOOL`で有効にする | `--brief`は通常の本文を画面から隠し全ての返答をツールへ求める。環境変数は公開された契約ではない |
| `$.ui.log`で本文を通知として表示する | 試作の画面で改行が崩れた薄い表示になった |
| `showThinkingSummaries`を`true`にする | ユーザーの設定を変え、表示されるのも要約である |
| 報告でターンを終え、後続を次の応答で再開する | 2026年10月6日の確認でユーザーが選ばなかった |
| `CLAUDE_CODE_THINKING_DISPLAY_UPDATES=false`か`CLAUDE_CODE_TURN_UPDATES`を設定する | 公開ドキュメントで記載を確認していない。前者はprogress updateが文面の空のまま返る側になり、後者は最後の本文の要約を促すだけで途中の原文を届けない |
| 署名を解析して要約を検出するStop hook | 署名を読む手順を使わない方針（2026年10月7日）と、要約への置換をhookで検出しない判断（2026年10月6日、ユーザー）に反する |

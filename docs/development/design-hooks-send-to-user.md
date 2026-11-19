# ユーザーへの本文送達の設計記録

[設計記録の索引](design.md)から参照する、ユーザーへの本文送達の記録である。

## Code Mode対応の送達

報告本文は既存agents_server MCPの`send_to_user`で受け、会話内modが`ui.place`で実描画を確認してから成功を返す。
MCP単独では描画できないという制約を、期限付きの要求と確認応答で解消した。
Code Modeで実行されない`tool.call`・`tool.check`イベントには登録しない。
メイン限定の判定はMCPの起動元と設定PreToolUseの`agent_id`・委譲環境印で行う。
本文の日本語確認はMCP、描画はmod、送達後の再掲禁止は既存Stop判定が所有する。

要求は会話IDとランダムな要求IDごとのファイルへ保存し、入力・期限・応答IDを対応付ける。
Python側が要求をatomicに書き、modが実配置の結果を返す。
未応答・配置不能・期限切れを成功にしない。
応答の回収は呼出側、非同期観測と期限切れの回収は同じ要求モジュールが所有し、別の常駐サービスを作らない。
描画本文と処理済みIDはmodの会話状態へ置き、稼働中の再読込で本文を失ったり二重配置したりしない。
会話状態と置いた行は会話の終了・`/clear`・`/resume`・`/branch`で初期化されるため、再開後の画面ではtranscriptに残る`send_to_user`のツール入力から、`ui.render`の`ToolUse`で元の位置へ本文を描く。
再開後にまとめられた`ToolGroup`は展開し、呼び出しごとに描く。
稼働中に置いた行がある呼出しのツール行は描き替えず、二重に表示しない。
置いた行がある呼出しの判定は、本文ではなく呼出しの`tool_use_id`で行う。
Claude CodeがMCP要求の`_meta`へ渡す`claudecode/toolUseId`を`send_to_user`が表示要求へ添え、modがその呼出しを会話状態へ記録する。
本文で判定すると、再開後に同じ本文を送ったときに再開前の呼出しの位置の本文まで消えるためである。
`_meta`に識別子が無い要求だけは本文の一致で判定する。
稼働中の呼出しの記録には`tool.call`の登録を使わない。
登録するとCode Modeを妨げるためである。
描画中の会話状態の書込みはホストが拒否するため、描画側でも記録しない。
本文の上下には1行の余白を置き、前後の会話行と区別する。
Code Modeの実ツール呼出しはtranscriptで`<synthetic>`のモデル名を持つ場合もあるため、API失敗の代替本文と送達ツールの入力を区別する。

表示の成功は`ui.place.isPlaced`で観測し、MCPの受付やツール呼出しの存在とは区別する。
同じ橋渡しへ圧縮・反映の予約と背景待機の観測を接続し、各操作の完了条件は分ける。
新しいモデル実行器、署名による送達判定、擬似ユーザー発話は追加しない。

ユーザー向け本文の画面表示は、transcriptへ原文のまま記録される内容（`send_to_user`のツール入力）だけを出所にする。
稼働中も、会話の終了・切替・再開の後も、その内容から元の位置へ表示できることを保つ。
送達を読む場面は、稼働中に読む場面と、再開した会話で読み返す場面である。
表示経路を変える場合は、両方の場面を受入の対象にする。

## 旧実装（2026年11月14日まで）

以下はCode Mode互換化前の構造と当時の比較である。
現在の登録名と責務は前節を使う。

### send_to_userツール

Claude Codeでは、同じ応答でツール呼び出しより前に置いた地の文の一部を、APIが要約（progress updateの要約）へ置き換えて返す。
原文は画面にもtranscriptにも残らず、メインは届いたとみなして作業を続ける。
事象は`incidents-reporting.md`の2026年10月6日の項にある。

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
ツールの登録と表示を同じmoduleに置くため、プラグインのmoduleを読み込まないClaude CodeとCodexにはツールが現れない。
現行のCLIでは2.1.287以降のMods対応とプラグインの有効化が必要であり、`disableAllHooks`や組織の管理設定などによって読み込みが止まる場合がある。
旧`CLAUDE_CODE_ENABLE_FUNCTION_HOOKS`は同版以降無視されるため、有効化条件として使わない（[公式の条件](https://code.claude.com/docs/en/plugins/mods/overview#turn-mods-on-or-off)）。
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
質問の意味と確認手段は`agent-toolkit:atk-mode/playbooks/confirmation.md`と`agent-toolkit:atk-mode/references/delivery.md`で扱う。
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

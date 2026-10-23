# Stop/SubagentStopフック

品質基準の正式な提供元は`agent-basics:writing-standards`の`references/claude-hooks-stop.md`である。呼出元が指定した条件と読込範囲を、同名の正式な品質資料へ引き継ぐ。節指定がある場合は移設先の実見出しへ接続し、指定がない場合は従来の全文読込契約を保つ。

以下はagent-toolkitが所有する運用条件である。呼出元の条件と読込範囲に従い、ここに残る運用節を補う。

## 名指し参照の読込方法

本文の各参照の適用条件を保ち、選んだ資料の指定範囲を取得してから、その基準を使う具体的な判断へ進む。起動時の行の資料は起動本文とともに届く。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:writing-standards/references/claude-hooks-stop.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/claude-hooks-stop.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../delegation/references/claude-code-runtime.md#「実行時能力と通信scope」` | 判断 | `section:agent-toolkit:*/skills/delegation/references/claude-code-runtime.md#「実行時能力と通信scope」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../completion-report/references/termination-evidence.md` | 判断 | `read:agent-toolkit:*/skills/completion-report/references/termination-evidence.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../process-wi/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../add-awi-by-user/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/add-awi-by-user/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../completion-report/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/completion-report/SKILL.md` |

## 遮断の条件と再帰呼び出し対策

- 記述言語など本文から機械的に確定できる判定では、hookの通知に検出箇所、判定基準、誤検出時に本文を変えず終える操作を示す。ユーザーの画面へ本文を届けるClaude Codeの最上位メインでは`…`だけを出力する。委譲元へ本文を渡す主体（Claude Codeのサブエージェント、agents_serverの委譲先、Codex）は、成果・判断・未達と指定の返却形式を含む完了本文の全文をそのまま再出力して停止する。これらの主体では最後の応答が返却本文になる

## 入力

Claude Codeはassistantの`text`要素と`mcp__agent-toolkit__send_to_user`の文字列`message`を可視本文とする。

## Codexの差分

CodexのStopは`response_language_advisor`で共通入力`last_assistant_message`の説明言語を判定する。終了工程の起動要否と送達・残工程の点検は`agent-toolkit:completion-report`の文書で扱う。Claude Code固有のtranscript形式と非遮断通知を前提とする他のStop判定は、Codexの対象から除く。

## 委譲先での発火

- `Stop`はそのセッション自身のターン終端で発火する。`agents_server`が起動した委譲先は環境変数`AGENT_TOOLKIT_DELEGATED_SESSION`で判別する

- `SubagentStop`は`Agent`ツールのサブエージェントのターン終端だけで発火し、判別には入力の`agent_id`を用いる。前記の環境変数は`agents_server`が起動したセッションにだけ付く。`SubagentStop`へ登録するのはサブエージェントが実行できる処置に限る

区分の詳細は`agent-toolkit:delegation`の`references/claude-code-runtime.md`「実行時能力と通信scope」に従う。

## 終了工程の証拠

判定と保持の責務は`agent-toolkit/agent_toolkit/_hooks/termination_evidence.py`が所有する。設計の経緯と却下した案は`docs/development/design-hooks.md`「終了工程の証拠」にある。
プロジェクト規範による省略を記録するメインは、`agent-toolkit:completion-report`を起動せず同スキルの`references/termination-evidence.md`を直接読む。
意味と優先順位はメインが判断し、記録処理は指定したパスから規範全文を読んで保存し、所在、観測した作業の対象、現在のメインと会話・入力の対応を確かめる。
規範の変更、次の判断、新しい人間入力の後の成果作業では、省略の適用を改めて判断する。省略後も別作業の起動不足を判定し、process-loopの会話が終了時に`atk agents-exit-session`を実行する義務（`agent-toolkit:completion-report`の`references/termination-evidence.md`）を維持する。

- 供給と消費: PreToolUseとPostToolUse（Claude CodeではPostToolUseFailureを含む）が実際の呼び出しと応答を供給し、UserPromptSubmitが人間の入力を供給する。Claude CodeのStopの`termination_order_advisor`は同じ証拠で起動不足を通知する。報告本文を除いた証拠で起動不足を判定する
- 判定する範囲: 成果作業（成功した`agent-toolkit:process-wi`・`agent-toolkit:add-awi-by-user`の開始、リポジトリへの自編集、計画の保存、WIの保存、commit・push）を行った作業を対象とする。起動を要する作業に`agent-toolkit:completion-report`の起動証拠が無ければ、共通noticeで同じ原因・対象・対処の反復を抑えながら非遮断通知を返す。適用するプロジェクト規範による有効な省略は、その作業の起動不足だけを免除する。送達・残工程の点検と報告の内容・書式はcompletion-reportの規範が扱う
- 起動証拠: Claude Codeは成功したSkill呼び出し、CodexはBashへ正規化された読取コマンドの入力が`skills/completion-report/SKILL.md`を指し、その結果が同スキルのfrontmatterの`name`を含む読取とする。読取パスの文字列、起動するという発話、失敗・空の読取、検索の一致行、他の作業や委譲先の起動は証拠にしない。コマンドの対象は`agent-toolkit/agent_toolkit/_common/shell_segments.py`の`extract_execution_segments`が取り出した実行位置で識別し、検索語や引用の中のコマンド名は対象外とする
- 待機の許可: 起動証拠を欠く作業でも、その作業が待つ非同期対象が生存する間は終了を許可する。待機対象は作業が起動したagents_serverのsessionと、作業の開始以後に起動が記録された非同期対象（背景Agent・MCPのバックグラウンドタスク・未配送の完了通知・背景Bash）である。終了する背景Bashは完了通知でターンが再開し、その時点で改めて判定される。実行位置から既知のサーバー・watch起動と判別できる背景Bashは待機免除から除き、起動不足を判定する。有限の長時間コマンドと待機CLIの免除は保ち、任意の自作コマンドの常駐性は静的に確定しない。作業の開始前から動くタスクは待機対象から外す。作業との対応はStop側で判定し、他のhookが使う`background_tasks.is_pending_async_work`の意味を変えない
- 作業の区切り: ユーザーの入力、呼び出しの単位、開始と判断記録で区切る。起動証拠を得た作業の後に新しい人間の入力があり、続けて成果作業が届いた場合は新しい作業とし、前の起動証拠を流用しない
- 判断の記録: 中止・置換・開始・再開・確認待ち・委譲先の待機・技術的不成立は、メインが`atk run-script termination-evidence`で記録する。意味はメインが決め、記録処理は識別子から実体を読み、原入力の由来、対象の実在と主体だけを確かめる。Stopの継続入力、`atk-auto`、構造上の生成標識を持つ入力と`source`が`user`以外の入力は人間の根拠から除く。完了の申告は記録操作の対象外とする。現在の作業を中止・置換・技術的不成立と記録しても、他の作業に残る起動不足は判定する
- 証拠を読めない場合（状態の破損、旧版の未供給、期限回収後、transcriptを読めず待機対象を確かめられない場合）は完了とも未完了とも扱わず、Stop判定ログへ診断を残して許可する

# Stop/SubagentStopフック

本書はStop/SubagentStopフックの遮断条件、再帰呼び出し対策、入力の扱い、Codexの差分、委譲先での発火と終了工程の証拠を定める。

## 遮断の条件と再帰呼び出し対策

フックがターン終了を阻止するとエージェントは新たな応答を生成し、同じフックが再び発火する。判定条件が変化しなければ、Claude Codeの連続block上限（`CLAUDE_CODE_STOP_HOOK_BLOCK_CAP`で変更可）まで繰り返し、上限で判定が無視されてターンが終了する。

- Stopの集約エントリーポイントは連続blockの上限を管理し、到達したら遮断を打ち切って、その事実と遮断していた判定名をStop判定ログへ記録する。個々の判定は、`stop_hook_active`（直前の同フック呼び出しがそのターンの終了を一度阻止したこと）と自身の条件で継続の可否を決める
- `decision: "block"`と`additionalContext`のどちらでも、対象のエージェントが同一ターン内の行動で解消できる条件に限る。外部事象の完了、他のエージェントの稼働状態、直前ターンで確定済みの内容は条件から外す。終了を拒否された待機中のエージェントは、無操作のツール呼び出しを反復する。blockと同時にフック自身が条件の元の状態を取り下げる1回限りのblock（process-loopでバックグラウンドタスクが残る間の終了要求の取り下げなど）は、反復を生まないため対象外とする
- `additionalContext`は想定内の助言としてtranscriptへ表示され、フックのエラー通知の対象外である
- 応答本文の語や文字の一致・比率から本文の意味や性質を推定して終了を止める判定では、hookが返す遮断の通知本文に、検出した箇所、判定基準、基準を満たさない場合に本文を変えずに終える操作を示す。Claude Codeのメインでは`…`だけを出力する。CodexのStop・SubagentStopでは完了本文の全文をそのまま再出力して停止する。Codexは最後の応答が完了本文になるため、成果・判断・未達と指定の返却形式を再出力へ保つ。誤検出の是正は、判定条件へ除外語を追加する前に、通知が示す基準と終える操作で解消できるかを確かめる

## 入力

`Stop`と`SubagentStop`の共通入力は`session_id`・`prompt_id`・`transcript_path`・`cwd`・`permission_mode`・`effort`・`hook_event_name`である。
両イベントはこれに加えて`stop_hook_active`・`last_assistant_message`・`background_tasks`・`session_crons`を受け取る。
`SubagentStop`はさらに`agent_id`・`agent_type`・`agent_transcript_path`を受け取り、その`background_tasks`と`session_crons`は親セッションの範囲を表す。
`background_tasks`は実行中のタスクごとに`id`・`type`・`status`・`description`を持ち、`type`は生成した機能（`shell`・`subagent`・`monitor`・`workflow`・`teammate`・`cloud session`・`MCP task`）を示す。セッションの完了と、背景の作業による再開待ちの停止を区別する用途で使う。
`PostToolUse`は背景実行への移行の時点で発火し、ジョブ完了時の再発火は公式ドキュメントに記載が無い。
監査記録は`docs/development/audit-records.md`の「agent-toolkit/skills/writing-standards/references/claude-hooks-stop.md：入力：2026年9月4日」にある。現行版の入力に`background_tasks`が現れない場合は本項を失効させ、その版の観測として書き直す。

可視本文を読む判定（`user_response_advisor`など）は、`last_assistant_message`と現行形式のtranscriptからassistantの可視本文だけを取り出す。
Claude Codeはassistantの`text`要素と`mcp__agent-toolkit__send_to_user`の文字列`message`を可視本文とする。
読めない記録と取得できない記録は未発話と扱わず、判定不能をStop判定ログへ残して許可する。工程の完了はtranscriptから推定せず、終了工程の証拠から判定する。

## Codexの差分

CodexのStopは`decision: "block"`と`reason`で同一ターンを継続し、許可時は空のJSONオブジェクトを返す（`hookSpecificOutput`は受理されないため）。
Codex固有の入力には`model`がある。Codex rolloutのtranscript形式は安定インターフェースではないため、完了判定やバックグラウンドタスクの判定の入力にせず、状態欠落時の回復判定などで標識の有無を確認する用途に限ってruntime別に変換する。
CodexのStopは`response_language_advisor`と`termination_order_advisor`を実行する。前者は共通入力`last_assistant_message`の説明言語を、後者は終了工程の起動証拠を判定する。Claude Code固有のtranscript形式や通知手段を前提とする他のStop判定は、Codexの対象から除く。

## 委譲先での発火

`Stop`と`SubagentStop`の判定は、いずれも委譲先で発火し得る。判定が求める処置を委譲先が実行できるかを判定ごとに確定し、結果と根拠をその判定モジュールのdocstringへ記録する。実行できない処置を求める判定は委譲先を対象から除く。

- `Stop`はそのセッション自身のターン終端で発火する。`agents_server`が起動した委譲先は環境変数`AGENT_TOOLKIT_DELEGATED_SESSION`で判別する
- `SubagentStop`は`Agent`ツールのサブエージェントのターン終端だけで発火し、判別には入力の`agent_id`を用いる。前記の環境変数は`agents_server`が起動したセッションにだけ付く。`SubagentStop`へ登録するのはサブエージェントが実行できる処置に限る

区分の詳細は`agent-toolkit:delegation`の`references/claude-code-runtime.md`「実行時能力と通信scope」に従う。

## 終了工程の証拠

判定と保持の責務は`agent-toolkit/agent_toolkit/_hooks/termination_evidence.py`が所有する。設計の経緯と却下した案は`docs/development/design-hooks.md`「終了工程の証拠」にある。
プロジェクト規範による省略を記録するメインは、`agent-toolkit:completion-report`を起動せず同スキルの`references/termination-evidence.md`を直接読む。
意味と優先順位はメインが判断し、記録処理は指定したパスから規範全文を読んで保存し、所在、観測した作業の対象、現在のメインと会話・入力の対応を確かめる。
規範の変更、次の判断、新しい人間入力の後の成果作業では、省略の適用を改めて判断する。省略後も別作業の起動不足を判定し、process-loopの会話が終了時に`atk agents-exit-session`を実行する義務（`agent-toolkit:completion-report`の`references/termination-evidence.md`）を維持する。

- 供給と消費: PreToolUseとPostToolUse（Claude CodeではPostToolUseFailureを含む）が実際の呼び出しと応答を供給し、UserPromptSubmitが人間の入力を供給する。Stopの`termination_order_advisor`は同じ証拠だけを消費し、報告本文は消費対象から除く
- 判定する範囲: 成果作業（成功した`agent-toolkit:process-wi`・`agent-toolkit:add-awi-by-user`の開始、リポジトリへの自編集、計画の保存、WIの保存、commit・push）を行った作業を対象とする。起動を要する作業に`agent-toolkit:completion-report`の起動証拠が無い終了を、初回Stopから遮断する。適用するプロジェクト規範による有効な省略は、その作業の起動不足だけを免除する。起動後の工程の完遂、報告の内容と書式は判定せず、completion-reportの規範が扱う
- 起動証拠: Claude Codeは成功したSkill呼び出し、CodexはBashへ正規化された読取コマンドの入力が`skills/completion-report/SKILL.md`を指し、その結果が同スキルのfrontmatterの`name`を含む読取とする。読取パスの文字列、起動するという発話、失敗・空の読取、検索の一致行、他の作業や委譲先の起動は証拠にしない。コマンドの対象は`agent-toolkit/agent_toolkit/_common/shell_segments.py`の`extract_execution_segments`が取り出した実行位置で識別し、検索語や引用の中のコマンド名は対象外とする
- 待機の許可: 起動証拠を欠く作業でも、その作業が待つ非同期対象が生存する間は終了を許可する。待機対象は、作業が起動したagents_serverのsessionと、作業の開始以後に起動が記録された非同期対象（背景Agent・MCPのバックグラウンドタスク・未配送の完了通知・背景Bash）である。終了する背景Bashは完了通知でターンが再開し、その時点で改めて判定される。実行位置から既知のサーバー・watch起動と判別できる背景Bashは待機免除から除き、起動不足を判定する。有限の長時間コマンドと待機CLIの免除は保ち、任意の自作コマンドの常駐性は静的に確定しない。作業の開始前から動くタスクは待機対象から外す。作業との対応はStop側で判定し、他のhookが使う`background_tasks.is_pending_async_work`の意味を変えない
- 作業の区切り: ユーザーの入力、呼び出しの単位、開始と判断記録で区切る。起動証拠を得た作業の後に新しい人間の入力があり、続けて成果作業が届いた場合は新しい作業とし、前の起動証拠を流用しない
- 判断の記録: 中止・置換・開始・再開・確認待ち・委譲先の待機・技術的不成立は、メインが`atk run-script termination-evidence`で記録する。意味はメインが決め、記録処理は識別子から実体を読み、原入力の由来、対象の実在と主体だけを確かめる。Stopの継続入力、`atk-auto`、構造上の生成標識を持つ入力と`source`が`user`以外の入力は人間の根拠から除く。完了の申告を記録する操作は設けない。現在の作業を中止・置換・技術的不成立と記録しても、他の作業に残る起動不足は判定する
- 証拠を読めない場合（状態の破損、旧版の未供給、期限回収後、transcriptを読めず待機対象を確かめられない場合）は完了とも未完了とも扱わず、Stop判定ログへ診断を残して許可する

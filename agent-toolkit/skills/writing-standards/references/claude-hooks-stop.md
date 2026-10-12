# Stop/SubagentStopフック

本書はStop/SubagentStopフックの遮断条件、再帰呼び出し対策、入力の扱い、Codexの差分、委譲先での発火と終了工程の証拠を定める。

## 遮断の条件と再帰呼び出し対策

フックがターン終了を阻止するとエージェントは新たな応答を生成し、同じフックが再び発火する。判定条件が変化しなければ、Claude Codeの連続block上限（`CLAUDE_CODE_STOP_HOOK_BLOCK_CAP`で変更可）まで繰り返し、上限で判定が無視されてターンが終了する。

- Stopの集約エントリーポイントは連続blockの上限を管理し、到達したら遮断を打ち切って、その事実と遮断していた判定名をStop判定ログへ記録する。個々の判定は、`stop_hook_active`（直前の同フック呼び出しがそのターンの終了を一度阻止したこと）と自身の条件で継続の可否を決める
- `decision: "block"`と`additionalContext`のどちらでも、対象のエージェントが同一ターン内の行動で解消できる条件に限る。外部事象の完了、他のエージェントの稼働状態、直前ターンで確定済みの内容は条件から外す。終了を拒否された待機中のエージェントは、無操作のツール呼び出しを反復する。blockと同時にフック自身が条件の元の状態を取り下げる1回限りのblock（process-loopでバックグラウンドタスクが残る間の終了要求の取り下げなど）は、反復を生まないため対象外とする
- `additionalContext`は想定内の助言としてtranscriptへ表示され、フックのエラー通知を伴わない
- ターン終了の言語的判定（完了文言・質問・待機表明の判別）をコードの正規表現などで行うと誤検知しやすい。誘導文の先頭に判定基準を置き、基準を満たさない場合は誘導に従わずターンを終了させる設計を推奨する

## 入力

`Stop`と`SubagentStop`の共通入力は`session_id`・`prompt_id`・`transcript_path`・`cwd`・`permission_mode`・`effort`・`hook_event_name`である。
両イベントはこれに加えて`stop_hook_active`・`last_assistant_message`・`background_tasks`・`session_crons`を受け取る。
`SubagentStop`はさらに`agent_id`・`agent_type`・`agent_transcript_path`を受け取り、その`background_tasks`と`session_crons`は親セッションの範囲を表す。
`background_tasks`は実行中のタスクごとに`id`・`type`・`status`・`description`を持ち、`type`は生成した機能（`shell`・`subagent`・`monitor`・`workflow`・`teammate`・`cloud session`・`MCP task`）を示す。セッションの完了と、背景の作業による再開待ちの停止を区別する用途で使う。
`PostToolUse`は背景実行への移行の時点で発火し、ジョブ完了時の再発火は公式ドキュメントに記載が無い。
監査記録は`docs/development/audit-records.md`の「agent-toolkit/skills/writing-standards/references/claude-hooks-stop.md：入力：2026年9月4日」にある。現行版の入力に`background_tasks`が現れない場合は本項を失効させ、その版の観測として書き直す。

報告本文の送達の判定は、`last_assistant_message`と現行形式のtranscriptからassistantの可視本文だけを取り出す限定用途とする。
Claude Codeはassistantの`text`要素と`mcp__agent-toolkit__send_to_user`の文字列`message`を可視本文とする。
Codexは`response_item`の`message`のうち`role`が`assistant`で`channel`が無いか`final`・`commentary`の`output_text`を可視本文とする。思考、ツール結果、人間入力、サイドチェーンと委譲先の記録は除く。
読めない記録と取得できない記録は未発話と扱わず、判定不能をStop判定ログへ残して許可する。工程の完了はtranscriptから推定せず、終了工程の証拠から判定する。

## Codexの差分

CodexのStopは`decision: "block"`と`reason`で同一ターンを継続し、許可時は空のJSONオブジェクトを返す（`hookSpecificOutput`は受理されないため）。
Codex固有の入力には`model`がある。Codex rolloutのtranscript形式は安定インターフェースではないため、完了判定やバックグラウンドタスクの判定の入力にせず、状態欠落時の回復判定などで標識の有無を確認する用途に限ってruntime別に変換する。
CodexのStopは`termination_order_advisor`だけを実行する。他のStop判定はClaude Codeの記録形式と通知手段を前提とする。

## 委譲先での発火

`Stop`と`SubagentStop`の判定は、いずれも委譲先で発火し得る。判定が求める処置を委譲先が実行できるかを判定ごとに確定し、結果と根拠をその判定モジュールのdocstringへ記録する。実行できない処置を求める判定は委譲先を対象から除く。

- `Stop`はそのセッション自身のターン終端で発火する。`agents_server`が起動した委譲先は環境変数`AGENT_TOOLKIT_DELEGATED_SESSION`で判別する
- `SubagentStop`は`Agent`ツールのサブエージェントのターン終端だけで発火し、判別には入力の`agent_id`を用いる。前記の環境変数は`agents_server`が起動したセッションにだけ付く。`SubagentStop`へ登録するのはサブエージェントが実行できる処置に限る

区分の詳細は`agent-toolkit:delegation`の`references/claude-code-runtime.md`「実行時能力と通信scope」に従う。

## 終了工程の証拠

判定と保持の責務は`agent-toolkit/agent_toolkit/_hooks/termination_evidence.py`が所有する。設計の経緯と却下した案は`docs/development/design-hooks.md`「終了工程の証拠」にある。

- 供給と消費: PreToolUseとPostToolUse（Claude CodeではPostToolUseFailureを含む）が実際の呼び出しと応答を供給し、UserPromptSubmitが人間の入力を供給する。Stopの`termination_order_advisor`は同じ証拠とメインの可視発話を消費する
- 証拠とする呼び出しは`atk run-script session-review-prepare`である。シェルのコマンドから`agent-toolkit/agent_toolkit/_common/shell_segments.py`の`extract_execution_segments`が取り出した実行位置にある場合だけ識別し、検索語や引用の中のコマンド名は対象外とする
- 報告段階は可視本文のH2（作業完了・振り返り結果の予告・振り返り結果・AWI投入結果）で区別し、本文には`agent-toolkit:completion-report`が定める報告本文の判定だけを適用する。固定の完了文そのものを遮断条件にしない
- 待機の許可: 報告段階が残る作業でも、その作業が待つ非同期対象が生存する間は終了を許可する。待機対象は、作業が起動したagents_serverのsessionと、作業の開始以後に起動が記録された非同期対象（背景Agent・MCPのバックグラウンドタスク・未配送の完了通知・公開の待機コマンド`atk agents wait`・`wait_ci.py`を実行する背景Bash）である。作業の開始前から動くタスクと、待機コマンドでない背景Bash（開発サーバーなどの常駐コマンド）は待機対象から外す。常駐コマンドは終了も完了通知もしないため、含めると報告が欠けたまま終了する。作業との対応はStop側で判定し、他のhookが使う`background_tasks.is_pending_async_work`の意味を変えない
- 作業の区切り: ユーザーの入力、呼び出しの単位、開始と判断記録で区切る。報告は主体別の送達規範に従い、起草ファイルや確認コマンドの出力を送達の証拠にしない
- 判断の記録: 中止・置換・開始・再開・確認待ち・委譲先の待機・技術的不成立は、メインが`atk run-script termination-evidence`で記録する。意味はメインが決め、記録処理は原入力の由来と全文の一致、対象の実在と主体だけを確かめる。Stopの継続入力、`atk-auto`、構造上の生成標識を持つ入力と`source`が`user`以外の入力は人間の根拠から除く。完了の申告を記録する操作は設けない。現在の作業を中止・置換・技術的不成立と記録しても、他の作業に残る報告段階は判定する
- 証拠を読めない場合（状態の破損、旧版の未供給、期限回収後）は完了とも未完了とも扱わず、Stop判定ログへ診断を残して遮断しない

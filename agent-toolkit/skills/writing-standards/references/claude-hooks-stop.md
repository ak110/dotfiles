# Stop/SubagentStopフック

本書はStop/SubagentStopフックの遮断条件、再帰呼び出し対策、入力の扱いと終了工程の証拠を定める。

## Stop/SubagentStopフックの再帰呼び出し対策

Stopの集約エントリーポイントは連続blockの上限を管理し、上限へ到達した場合に遮断を打ち切る。
個々の判定は、入力payloadの`stop_hook_active`に加えて自身の判定条件で継続の可否を決める。
上限到達時は、打ち切りの事実と遮断していた判定名をStop判定ログへ記録する。
`stop_hook_active`は直前の同フック呼び出しがそのターンの終了を一度阻止したことを示す。

Stop/SubagentStopの`decision: "block"`は対象主体が同一ターン内の行動で解消できる条件に限る。
外部事象の完了、他主体の稼働状態、直前ターンで確定済みの内容など、対象主体の行動で変えられない状態は条件に含めない。
待機中の主体が終了を拒否されると、ターンを終える以外の動作が残らず、無操作のツール呼び出しの反復に陥る。
blockと同時にフック自身が条件の元の状態を取り下げ、次のStopで同じ条件が成立しない1回限りのblock（process-loopでバックグラウンドタスクが残る間の終了要求の取り下げなど）は、反復を生まないためこの制限の対象外とする。

この対策を要する理由は次のとおりである。
フックがターン終了を阻止するとコーディングエージェントは新たな応答を生成し、
その応答に対して同じフックが再び発火する。
判定条件が変化しない場合、この繰り返しはClaude Codeの連続block上限まで続く。
上限に達すると警告とともにフックの判定が無視されてターンが終了する。
上限値は`CLAUDE_CODE_STOP_HOOK_BLOCK_CAP`環境変数で変更できる。

Stop・SubagentStopの`additionalContext`と`decision: "block"`の違いは`additionalContext`がフックの想定内の助言としてtranscriptへ表示され、フックのエラー通知を伴わない点である。前段の同一ターン内で解消できる条件の規定は両形式へ適用する。

ターン終了の言語的判定（完了文言・質問・待機表明の判別）をフック側のコードで
正規表現等により行うと誤検知が生じやすい。
コーディングエージェントへの誘導文の先頭に判定基準を事前チェックとして埋め込み、
基準を満たさない場合は誘導内容に従わずターンを終了する設計を推奨する。

`Stop`と`SubagentStop`の共通入力は`session_id`・`prompt_id`・`transcript_path`・`cwd`・`permission_mode`・`effort`・`hook_event_name`である。
両イベントはこれに加えて`stop_hook_active`・`last_assistant_message`・`background_tasks`・`session_crons`を受け取る。
`SubagentStop`はさらに`agent_id`・`agent_type`・`agent_transcript_path`を受け取る。
`SubagentStop`の`background_tasks`と`session_crons`は親セッションの範囲を表す。
`background_tasks`は実行中のタスクを1件ずつ表し、各要素は`id`・`type`・`status`・`description`を持つ。
`type`は`shell`・`subagent`・`monitor`・`workflow`・`teammate`・`cloud session`・`MCP task`のいずれかを取る。
値はそのタスクを生成した機能を示す。
この配列はセッションが完了した状態と、背景の作業による再開を待って停止している状態とをフックが区別する用途で使う。
`PostToolUse`は背景実行への移行の時点で発火する。そのジョブの完了時に再発火するという記載は公式ドキュメントに無い。
監査記録は`docs/development/audit-records.md`にある。
該当する見出しは「agent-toolkit/skills/writing-standards/references/claude-hooks-stop.md：Stop/SubagentStopフックの再帰呼び出し対策：2026年9月4日」である。
現行版の入力に`background_tasks`が現れない場合は本項を失効させ、その版の観測として書き直す。

CodexのStopは`decision: "block"`と`reason`で同一ターンを継続し、許可時は空のJSONオブジェクトを返す。
CodexのStopへ`hookSpecificOutput`を返しても受理されないため、前段の2つの形式だけを使う。
Codex固有の入力には`model`があり、Stopでは`stop_hook_active`と`last_assistant_message`も受け取る。
Codex rolloutのtranscript形式は安定インターフェースではないため、完了判定やバックグラウンドタスクの判定の取り決めの入力から外す。
状態欠落時の回復判定など、必要な標識の有無を確認する限定用途でだけruntime別に変換する。
報告本文が発話されたかの判定は、Stop入力の`last_assistant_message`を主な入力とし、それ以前の途中報告に限って現行形式のtranscriptからassistantの可視本文だけを取り出す限定用途とする。
Claude Codeはassistantの`text`要素、Codexは`response_item`の`message`のうち`role`が`assistant`で`channel`が無いか`final`・`commentary`の`output_text`を可視本文とする。
思考、ツール結果、人間入力、サイドチェーンと委譲先の記録は可視本文から除く。
形式を読めない記録と取得できない記録は未発話と扱わず、判定できなかったことをStop判定ログへ残して許可する。
工程の完了はtranscriptから推定せず、終了工程の証拠から判定する。

### 終了工程の証拠

終了工程の証拠は、`agent-toolkit/agent_toolkit/_hooks/termination_evidence.py`が判定と保持の責務を所有する。
PreToolUseとPostToolUse（Claude CodeではPostToolUseFailureを含む）が実際の呼び出しと応答を、UserPromptSubmitが人間の入力を供給する。
Stopの`termination_order_advisor`は同じ証拠とメインの可視発話を消費する。
証拠とする呼び出しは`atk run-script session-review-prepare`である。シェルのコマンドから`agent-toolkit/agent_toolkit/_hooks/bash_command_parser.py`の`extract_execution_segments`が取り出した実行位置にある場合だけ識別する。検索語や引用の中のコマンド名は呼び出しの対象外とする。
報告段階はStop入力の`last_assistant_message`とtranscriptのassistant可視本文のH2から取得する。作業完了・振り返り結果・AWI投入結果の見出しで段階を区別し、本文には`agent-toolkit:completion-report`が定める報告本文の判定だけを適用する。取得不能は診断を残して非遮断とする。現在の作業を中止・置換・技術的不成立と記録した場合も、他の作業に残る報告段階は判定する。
Stopは報告段階が残る作業に対し、その作業が待つ非同期対象が生存する間だけ終了を許可する。待機対象とするのは、作業が起動したagents_serverのsessionと、作業の開始以後にtranscriptへ起動が記録された非同期対象とする。非同期対象には背景Agent・MCPのバックグラウンドタスク・未配送の完了通知と、結果を待つ公開の待機コマンド（`atk agents wait`・`wait_ci.py`）を実行する背景Bashを含める。作業の開始より前から動くタスクと、作業内で背景起動した待機コマンドでないBash（開発サーバーなどの常駐コマンド）は待機対象から外し、報告の不足があれば遮断する。常駐コマンドは終了せず完了通知による再開も来ないため、待機対象に含めると報告が欠けたまま終了する。作業との対応はStop側で判定し、他のhookがセッション全体の継続判定に使う`stop_gate.is_pending_async_work`の意味を保つ。
報告は直接発話し、起草ファイルや確認コマンドの出力を送達の証拠へ使わない。作業はユーザーの入力、呼び出しの単位、開始と判断記録で区切り、全段階を満たした作業の後に新しい入力が届いてから始まった呼び出しは新しい作業へ割り当てる。
中止・置換・開始・再開・確認待ち・委譲先の待機・技術的不成立は、メインが`atk run-script termination-evidence`で記録する判断として扱う。判断の意味はメインが決め、記録処理は原入力の由来と全文の一致、対象の実在と主体を確かめる。Stopの継続入力、`atk-auto`、構造上の生成標識を持つ入力と`source`が`user`以外の入力は、人間の根拠から除く。完了の申告を記録する操作は設けない。
証拠を読めない場合（状態の破損、旧版の未供給、期限回収後）は完了とも未完了とも扱わず、Stop判定ログへ診断を残して遮断しない。証拠の再供給で回復できる。
CodexのStopは`termination_order_advisor`だけを実行する。他のStop判定はClaude Codeの記録形式と通知手段を前提とするため、Claude Codeだけへ適用する。

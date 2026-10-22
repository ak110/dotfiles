# Stop/SubagentStopフック

本書はStop/SubagentStopフックの遮断条件、再帰呼び出し対策、入力の扱い、Codexの差分と委譲先での発火を定める。

## 遮断の条件と再帰呼び出し対策

フックがターン終了を阻止するとエージェントは新たな応答を生成し、同じフックが再び発火する。判定条件が変化しなければ、Claude Codeの連続block上限（`CLAUDE_CODE_STOP_HOOK_BLOCK_CAP`で変更可）まで繰り返し、上限で判定が無視されてターンが終了する。

- Stopの集約エントリーポイントは連続blockの上限を管理し、到達したら遮断を打ち切って、その事実と遮断していた判定名をStop判定ログへ記録する。個々の判定は、`stop_hook_active`（直前の同フック呼び出しがそのターンの終了を一度阻止したこと）と自身の条件で継続の可否を決める
- `decision: "block"`と`additionalContext`のどちらでも、対象のエージェントが同一ターン内の行動で解消できる条件に限る。外部事象の完了、他のエージェントの稼働状態、直前ターンで確定済みの内容は条件から外す。終了を拒否された待機中のエージェントは、無操作のツール呼び出しを反復する。blockと同時にフック自身が条件の元の状態を取り下げる1回限りのblockは、反復を生まないため対象外とする
- `additionalContext`は想定内の助言としてtranscriptへ表示され、フックのエラー通知の対象外である
- 応答本文の語や文字の一致・比率から意味や性質を推定して停止を止める場合は、通知に検出箇所、判定基準、誤検出時に本文を変えず終える操作を示す。委譲元へ最後の応答が返却される主体では、成果・判断・未達と指定形式を保った全文を返せることを条件とする。判定の是正は除外語追加の前に、この解消操作で回復できるか確かめる

## 入力

`Stop`と`SubagentStop`の共通入力は`session_id`・`prompt_id`・`transcript_path`・`cwd`・`permission_mode`・`effort`・`hook_event_name`である。
両イベントはこれに加えて`stop_hook_active`・`last_assistant_message`・`background_tasks`・`session_crons`を受け取る。
`SubagentStop`はさらに`agent_id`・`agent_type`・`agent_transcript_path`を受け取り、その`background_tasks`と`session_crons`は親セッションの範囲を表す。
`background_tasks`は実行中のタスクごとに`id`・`type`・`status`・`description`を持ち、`type`は生成した機能（`shell`・`subagent`・`monitor`・`workflow`・`teammate`・`cloud session`・`MCP task`）を示す。セッションの完了と、背景の作業による再開待ちの停止を区別する用途で使う。
`PostToolUse`は背景実行への移行の時点で発火し、ジョブ完了時の再発火は公式ドキュメントに記載が無い。
監査記録は`docs/development/audit-records.md`の「agent-basics/skills/writing-standards/references/claude-hooks-stop.md：入力：2026年9月4日」にある。現行版の入力に`background_tasks`が現れない場合は本項を失効させ、その版の観測として書き直す。

可視本文を読む判定は、`last_assistant_message`と現行形式のtranscriptからassistantの可視本文だけを取り出す。
Claude Codeではassistantの`text`要素から可視本文を取り出す。別の送信ツールも使う場合は、そのツールが渡す本文を同じ判定へ含める。
読めない記録と取得できない記録は未発話と扱わず、判定不能をStop判定ログへ残して許可する。工程の完了はtranscriptから推定せず、実際の工程の実行と結果の証拠から判定する。

## Codexの差分

CodexのStopは`decision: "block"`と`reason`で同一ターンを継続し、許可時は空のJSONオブジェクトを返す（`hookSpecificOutput`は受理されないため）。
Codex固有の入力には`model`がある。Codex rolloutのtranscript形式は安定インターフェースではないため、完了判定やバックグラウンドタスクの判定の入力にせず、状態欠落時の回復判定などで標識の有無を確認する用途に限ってruntime別に変換する。

## 委譲先での発火

`Stop`と`SubagentStop`の判定はいずれも委譲先で発火し得る。判定が求める処置を委譲先が実行できるかを判定ごとに確定し、結果と根拠をその判定モジュールのdocstringへ記録する。実行できない処置を求める判定は委譲先を対象から除く。

- `Stop`はそのセッション自身のターン終端で発火する。委譲先かの判別には起動基盤が保証する識別情報を使う
- `SubagentStop`は`Agent`ツールのサブエージェントのターン終端だけで発火し、入力の`agent_id`で判別する。登録するのはサブエージェントが実行できる処置に限る

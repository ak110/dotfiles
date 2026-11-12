# 委譲先のコンテキスト境界

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

## コンテキスト境界

通常のサブエージェントは委譲元とは独立した別コンテキストで動作し、引き継がれる入力情報は委譲プロンプトに限定される（委譲元の会話履歴、読み込み済みファイル、起動済みスキルは入力対象に含まれない）。
下位のサブエージェントを起動する場合も同様である。
ホスト標準のfork機能を利用する場合のみ、起動時点までの会話履歴を引き継ぐ例外として扱い、その選択基準は`routing-fork.md`「会話を引き継ぐ委譲」に従う。
fork以外の委譲についてはコンテキストが完全に独立する前提で設計し、その判定基準は`agent-toolkit:delegation`の`references/base-contract.md`に準拠する。
Codex環境で`agents_server`が利用できず組み込みの委譲機能で代替する場合は、`spawn_agent`に`fork_turns: "none"`を明示的に指定する。
`agents_server`経由で起動した委譲先が受け取る情報は、委譲プロンプト、および`agent-toolkit:delegation`の`references/runtime-routing.md`「実行手段」が定める起動条件によって決定されるもののみである。

Codexネイティブの`spawn_agent`で起動した委譲先では、`fork_turns`によって選択される会話履歴、対象worktreeから自動読み込みされる`AGENTS.md`、ならびに`SubagentStart` hookによって追加される委譲先規範を、それぞれ個別の入力として区別して扱う。
会話履歴に規範が含まれていることをhook配送の代替とせず、共通の委譲先規範は`rules-subagent.md`を原本の定義として、Codex hookとagents_serverの通常委譲へ同期させる。
軽量起動には同書の返却契約短節だけを配送し、全文配送と区別する。
Claude Code固有の委譲先規範はCodexへ配送しない。
Codexでforkを選択する場合は、`spawn_agent`の`fork_turns`を省略するか`"all"`を指定する。
監査記録は`docs/development/audit-records-delegation.md`の「agent-toolkit/skills/delegation/references/sub-agents-context.md：コンテキスト境界：2026年9月27日」に記載されている。
役割文書を渡して起動する際は、`agent-toolkit:delegation`のSKILL.md「送信」に従い、会話履歴を引き継がずに実行する。

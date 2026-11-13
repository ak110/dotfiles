# Claude Code・Codex Hook実装ガイドライン

品質基準の正式な提供元は`agent-basics:writing-standards`の`references/claude-hooks.md`である。
呼び出し元の読込条件を引き継ぎ、本書の運用条件と条件に該当する正式な資料の全文を読む。
両方に同じ主題があれば、両方の全文を確認する。
範囲の選択と取得済み本文の再利用は`agent-basics:writing-standards/references/read-method.md`に従う。

以下はagent-toolkitが所有する運用条件である。

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

## hookスクリプトの基本プロトコル

`SessionStart`は`agents_server`の委譲先でも発火し、`SubagentStart`は`Agent`ツールのサブエージェント起動時のみ発火する。
`agent-toolkit/agent_toolkit/_hooks/rules_context.py`は、前者において環境変数`AGENT_TOOLKIT_DELEGATED_SESSION`と`AGENT_TOOLKIT_OWNER_SESSION`を参照する。
これにより委譲先を判別し、メインエージェント向けの条文を除外する。
後者ではサブエージェント向けの条文を追加する。
なお、`Stop`と`SubagentStop`の判別は`agent-toolkit:delegation/references/claude-code-runtime-communication.md`「委譲先での発火」に従う。

## 条件別資料

| 時点または条件 | 全文読む資料 |
| --- | --- |
| 環境変数を新設・変更する前、既存の環境変数の名称・用途・参照主体を確認する前 | `claude-hooks-environment.md` |

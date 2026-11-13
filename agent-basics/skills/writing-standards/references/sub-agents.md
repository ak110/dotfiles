# サブエージェント定義ファイルと`<役割名>.parent.md`・`<役割名>.subagent.md`の記述基準

本書は、`agents/`配下に配置するClaude Code用サブエージェント定義ファイルの新規作成・改訂、ならびにClaude CodeやCodexの委譲先が関与する手順書の作成・改訂において満たすべき基準を定めたものである。
`agents/`のClaude Code用frontmatterにおける項目名および指定可能な値は、公式ドキュメント（`https://code.claude.com/docs/ja/sub-agents.md`）の定義に準拠する。

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

## ディレクトリ構成

`agents/`ディレクトリ配下にはサブエージェント定義ファイルのみを配置する。
`agents/`配下は再帰的に走査され、サブディレクトリ内のファイルもサブエージェント定義として登録される（プラグインでは`<plugin-name>:<サブディレクトリ名>:<name>`として公開される）。
定義ファイルが参照する資料のうち、そのサブエージェントの工程でのみ参照するものは対応するスキルの`references/`配下に配置する。
複数のスキルや定義で共有する資料は、プラグイン内の共有用ディレクトリ（例: `share/`）に配置し、本文からはプラグインのrootを示す変数を前置したパス（Claude Codeでは`${CLAUDE_PLUGIN_ROOT}`）で参照する。

## 条件別資料

| 時点または条件 | 全文読む資料 |
| --- | --- |
| `<役割名>.parent.md`と`<役割名>.subagent.md`の構成・返却形式の設計・名前付きagent、`<役割名>.subagent.md`およびskillの責務分離・手順設計時の確認に関する判断・作業の前 | `sub-agents-contracts.md` |
| Claude Codeのfrontmatter・コンテキスト境界に関する判断・作業の前 | `sub-agents-claude.md` |

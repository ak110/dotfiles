# Hookの出力フィールド：組織向け設定

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

## 組織向け設定

Claude Code 2.1.304以降で`cc-plugin-sec-default`が有効になっている管理環境（Team・Enterpriseなど）では、ユーザーが導入したプラグインに含まれる一部のFunction hooksの実行が省略される。
すべての配布先で確実に実行する必要がある処理は、`hooks.json`のコマンドhookか、実行が省略されないFunction hooksイベントに配置する。
実行が省略される対象は以下のとおりであり、すべての配布先で必要な処理の配置先から除外する。
要約生成の省略など任意の最適化を登録する場合は、hookが実行されなくても標準の圧縮と原本からの復元を継続できるようにする。
組織側の制約を解除したり、最適化の実行を必須としたりしない。

- `classic.`で始まるイベント
- `prompt.section`、`prompt.context`、`prompt.attachment`、`prompt.compose`、`skill.prompt`、`attribution.text`、`session.compact`、`settings.read`
- doorが`attachment`、`hook-context`、`compaction`である`session.append`
監査記録は`docs/development/audit-records-hooks-protocol.md`の「agent-basics/skills/writing-standards/references/claude-hooks-output.md：組織向け設定：2026年10月20日」にある。

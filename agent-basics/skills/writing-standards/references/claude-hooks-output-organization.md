# Hookの出力フィールド：組織向け設定

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

## 組織向け設定

Claude Code 2.1.304以降で`cc-plugin-sec-default`が有効になっている管理環境（Team・Enterpriseなど）では、ユーザーが導入したプラグインに含まれる一部のFunction hooksの実行が省略される。
組織のmodとして認識される条件は、[公式の組織向け設定](https://code.claude.com/docs/en/plugins/mods/admin#install-your-organizations-mods-and-set-the-order)で版ごとに確認する。
2.1.324以降のremote marketplaceでは、管理設定の`enabledPlugins`・`extraKnownMarketplaces`と、`prependPlugins`または`appendPlugins`を組み合わせる。
マーケットプレイスの許可リストだけでは組織のmodとして認識されない。
すべての配布先で確実に実行する必要がある処理は、`hooks.json`のコマンドhookか、実行が省略されないFunction hooksイベントに配置する。
実行が省略される対象は以下のとおりであり、すべての配布先で必要な処理の配置先から除外する。
ガードの通知はイベント単位であり、省略対象外のdoorに限定した`session.append`でも通知される。
任意の最適化という理由だけで登録すると、組織の環境では最適化が実行されず、毎セッションの省略通知も残る。
省略される登録を維持する要件がある場合は、理由、通知の有無、代替で保証できる範囲を個別に記録する。
組織のmodとして認識されれば、この省略を解消できる。
組織側の制約を解除したり、最適化の実行を必須としたりしない。
コマンドPreCompactで要約を短くする指示は渡せるが、要約モデルの呼出し自体は省略できない。
Claude Code 2.1.325の実機では、背景Bashの完了通知は`session.receive`へ配送されず、`session.append`の`queue`と`delivery`で取得できた。
この版では完了検知を保つため`session.append`を維持し、組織ガードの省略通知を解消するには組織のmodとして登録する。

- `classic.`で始まるイベント
- `prompt.section`、`prompt.context`、`prompt.attachment`、`prompt.compose`、`skill.prompt`、`attribution.text`、`session.compact`、`settings.read`
- doorが`attachment`、`hook-context`、`compaction`である`session.append`
監査記録は`docs/development/audit-records-hooks-protocol.md`にある。
対象の節は「agent-basics/skills/writing-standards/references/claude-hooks-output-organization.md：組織向け設定：2026年11月15日」である。

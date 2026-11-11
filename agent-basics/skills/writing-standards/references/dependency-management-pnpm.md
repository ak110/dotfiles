# 依存管理：pnpm

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 |
| --- | --- |
| 外部部品の共通の版方針を判断へ用いる前 | `quality-principles-dependencies.md` |

## pnpm

- pnpm 12以降は、初回に`npm install -g pnpm@<版>`またはstandaloneで導入し、`packageManager`を`pnpm@<版>`で固定する。
  版更新はプロジェクト内で`pnpm self-update <版>`を実行し、`packageManager`と`pnpm-lock.yaml`先頭のboot文書を同時に更新する。
  版固定と取得物の検証には、boot文書の`packageManagerDependencies`・`integrity`とレジストリ署名を使う。
  `packageManager`の`+sha512.<hash>`はpnpm自身の検証には使われない。
  CIでは`pnpm install --frozen-lockfile`で不整合を失敗として検出する。
  公開待機は`minimumReleaseAge`（pnpm 11以降は指定がなければ1440分）が`self-update`にも適用される。
  同コマンドはプロジェクトの待機設定を使わないため、変更する場合はグローバル設定・環境変数・CLI指定を使う。
  npm・standaloneによる初回導入では、導入する版の公開後経過時間を別に確認する。
  根拠と再検証手順は`docs/development/audit-records-dependencies.md`の「agent-basics/skills/writing-standards/references/dependency-management.md：pnpm：2026年11月6日」にある。
- pnpm 10以前では、従来どおりCorepackを導入し、`packageManager`にSHAハッシュ値を保持して`corepack use pnpm@<version>`で更新する。
  Node.js 25以降はCorepackを同梱しないため、必要な環境では別途導入する。
- pnpmの特定バージョンでは`NPM_CONFIG_*`環境変数の読み取りが不安定であり、`pnpm config get`が環境変数の値を無視する事象が観測されている。
  そのため、環境変数経由での設定反映を確認する際は`npm config get`を用いる。
  監査記録は`docs/development/audit-records-dependencies.md`の「agent-basics/skills/writing-standards/references/dependency-management.md：pnpm：2026年9月16日」にある。
- `pnpm-workspace.yaml`内の設定項目は、`NPM_CONFIG_*`環境変数による指定よりも優先して適用される。

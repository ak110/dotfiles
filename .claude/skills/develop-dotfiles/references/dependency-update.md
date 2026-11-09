# 依存関係の更新

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| `make update`を実行の候補にする前 | 現行`Makefile`の`update` targetと、それが呼び出す子target | 判断 | `section:*/Makefile#make:update` |

## 自動チェックと依存更新

- `make update`は、読込表にあるターゲットの実際の処理対象に変更対象が含まれている場合のみ実行候補とする。
  現在の主な更新対象は`uv.lock`と`agent-toolkit/uv.lock`の依存関係更新である。
  lockファイルの更新後はコンテナーでの依存関係インストールを検証し（`ci-deps-check`）、さらに`prek autoupdate`、`mise lock`、`pinact`によるアクション更新、および全テストを実行する。
  `rust/claude-statusline/Cargo.lock`もCargo.tomlの制約内で更新し、fmt・clippy・testを実行する。
  GitHub Actionsのハッシュピンのみを更新する場合は`make update-actions`を実行する。

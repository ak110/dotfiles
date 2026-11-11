# Python記述スタイル

品質基準の正式な提供元は`agent-basics:writing-standards`の`references/python.md`である。
呼び出し元の読込条件を引き継ぎ、本書の運用条件と条件に該当する正式な資料の全文を読む。
両方に同じ主題があれば、両方の全文を確認する。
範囲の選択と取得済み本文の再利用は`agent-basics:writing-standards/references/read-method.md`に従う。

以下はagent-toolkit固有の運用条件である。

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

## 標準ライブラリとログ

- agent-toolkitパッケージ内でGitを起動するときは`agent_toolkit._git.command.run`を使う（`text=True`で渡した文字列の`input`も、UTF-8のバイト列として子プロセスへ渡される）。

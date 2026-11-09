# 画面の設計とユーザビリティの検収

品質基準の正式な提供元は、`agent-basics:writing-standards/references/ui-ux.md`である。
呼び出し元の読込条件を引き継ぎ、本書の運用条件と条件に該当する正式な資料の全文を読む。
両方に同じ主題があれば、両方の全文を確認する。
範囲の選択と取得済み本文の再利用は`agent-basics:writing-standards/references/read-method.md`に従う。

以下はagent-toolkitが所有する運用条件である。

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:writing-standards/references/ui-ux.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/ui-ux.md` |

## 完了前の実描画確認

画面キャプチャは、描画ツールが書き込める場所へ保存し、その絶対パスを記録する。
PlaywrightのライブラリやE2Eテスト環境を利用する場合は、managed-tempへ直接保存する。
保存先が制限されているブラウザ操作ツール（`@playwright/mcp`など）を利用する際は、ファイル名を指定せずに保存し、ツールから返されたパスを絶対パスとして解決して記録する。
ファイル名を指定すると作業ディレクトリを基準に解決され、出力ディレクトリや作業ディレクトリ外への書き込みが拒否されるためである。
出力ディレクトリが対象の作業ツリー内に位置する場合は、managed-tempへ移動してから記録する。

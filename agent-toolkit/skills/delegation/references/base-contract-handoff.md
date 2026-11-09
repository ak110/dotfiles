# 基本委譲契約：引き継ぎ記録先

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../managed-temp/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/managed-temp/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `handoff-record.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/handoff-record.md` |

## 引き継ぎ記録先

`<役割名>.subagent.md`で2つ以上の工程を順番に実行する委譲では、`引き継ぎ記録先: <絶対パス>（新規）`または`引き継ぎ記録先: <絶対パス>（継続）`の行を委譲プロンプトに含める。

- `（新規）`か`（継続）`かは、先行する担当者が該当パスにすでに記録を書き込んだかどうかのみで判断する（ファイルの作成自体は委譲先が行う）。
  新規か継続かが示されていない行を受け取ると、委譲先は存在しないファイルを読み込もうとして失敗する。
- パスは委譲元のセッションのmanaged-temp（`agent-toolkit:managed-temp`）直下のファイルとし、自動回収の対象から除外する。
- `agents_server`の`start`ツールで`<役割名>.subagent.md`を指定して起動する場合は、`（新規）`の指定を省略できる。
  サーバーが記録先を自動的に用意してプロンプトに追加し、`handoff_record_path`として返却する。
  `Agent`ツールなどサーバーを経由しない起動では、委譲元自身がこの行を記述する。
- 委譲先が応答を停止し、別の担当者へ工程を引き継ぐ場合は、前回の起動で使われたパス（省略時は返却された`handoff_record_path`の値）に`（継続）`を付与して引き渡す。

記録すべき具体的な内容は`references/handoff-record.md`で定める。

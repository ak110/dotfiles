# 表記とMarkdownの規則と確認手段

品質基準の正式な提供元は`agent-basics:writing-standards/references/notation-rules.md`である。呼び出し元の取得条件と注目箇所を引き継ぎ、本書と正式な資料を全文読む。節指定がある場合は、指定された見出しを持つ資料に注目する。両方に同名の見出しがあれば両方に注目する。

以下はagent-toolkit固有の運用条件である。

## 名指し参照の読込方法

名指し参照は`../SKILL.md`「名指し参照の読込方法」に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:writing-standards/references/notation-rules.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/notation-rules.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-toolkit:/share/check-execution-operations.md#「pyfltrの起動形」` | 判断 | `section:agent-toolkit:*/share/check-execution-operations.md#「pyfltrの起動形」` |

## チェックの対象範囲

- `atk wi add`や`atk wi edit`の`--body-file`引数へ渡すWI本文は、文章lintの必須チェック対象から除外する。口語表現やダッシュについては保存前の診断で行番号付きの警告が表示されるため、対応の要否は起草者が判断する（保存できるかどうかは構造エラーの有無で判定される）。なお、計画ファイルやWI本文から別の文書へ転記する文面は、転記先の基準に従ってチェックする。

## 参照資料の取得

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:check-execution/SKILL.md` | 判断 | `read:agent-basics:*/skills/check-execution/SKILL.md` |

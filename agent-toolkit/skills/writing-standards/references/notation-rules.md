# 表記とMarkdownの規則と確認手段

品質基準の正式な提供元は`agent-basics:writing-standards`の`references/notation-rules.md`である。呼び出し元の取得条件と注目箇所を引き継ぎ、本書と正式な資料を全文読む。節指定がある場合は、指定された見出しを持つ資料に注目する。両方に同名の見出しがあれば両方に注目する。

以下はagent-toolkit固有の運用条件である。

## 名指し参照の読込方法

名指し参照は`../SKILL.md`「名指し参照の読込方法」に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:writing-standards/references/notation-rules.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/notation-rules.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../check-execution/SKILL.md#「pyfltrの起動形」` | 判断 | `section:agent-toolkit:*/skills/check-execution/SKILL.md#「pyfltrの起動形」` |

## チェックの対象範囲

- `atk wi add`や`atk wi edit`の`--body-file`引数へ渡すWI本文は、文章lintの必須チェック対象から除外する。口語表現やダッシュについては保存前の診断で行番号付きの警告が表示されるため、対応の要否は起草者が判断する（保存できるかどうかは構造エラーの有無で判定される）。なお、計画ファイルやWI本文から別の文書へ転記する文面は、転記先の基準に従ってチェックする。

## 文章チェックの一括実行

MCPの`run`ツールを利用できない場合に限り、`agent-toolkit:check-execution`の「pyfltrの起動形」で解決したCLIを使い、同じ対象と制約を指定して実行する（外部ファイルを指定する場合は`--allow-external-paths`を加える）。チェックの完了は、出力JSONLの最終行にある`summary`レコードに対し、正式な品質資料に定められた全対象被覆の条件を適用して判定する。

日本語の地の文や見出しに含まれるダッシュは、プロジェクトのpyfltr設定に`writing-check-dash`として登録し、口語表現の確認やtextlintと同様に`run`の`commands`へ含める。
登録する実行手段には、複数の対象ファイルを受け取れる公開CLIである`atk run-script writing-check-dash -- <ファイル...>`を使用する。
検出範囲の基準は正式な品質資料が定め、その検出処理の実装は`agent-toolkit/skills/writing-standards/scripts/check_dash.py`に配置されている。

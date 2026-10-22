# 表記とMarkdownの規則と確認手段

品質基準の正式な提供元は`agent-basics:writing-standards`の`references/notation-rules.md`である。呼出元が指定した条件と読込範囲を、同名の正式な品質資料へ引き継ぐ。節指定がある場合は移設先の実見出しへ接続し、指定がない場合は従来の全文読込契約を保つ。

以下はagent-toolkitが所有する運用条件である。呼出元の条件と読込範囲に従い、ここに残る運用節を補う。

## 名指し参照の読込方法

本文の各参照の適用条件を保ち、選んだ資料の指定範囲を取得してから、その基準を使う具体的な判断へ進む。起動時の行の資料は起動本文とともに届く。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:writing-standards/references/notation-rules.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/notation-rules.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../check-execution/SKILL.md#「pyfltrの起動形」` | 判断 | `section:agent-toolkit:*/skills/check-execution/SKILL.md#「pyfltrの起動形」` |

## チェックの対象範囲

- `atk wi add`・`atk wi edit`の`--body-file`へ渡すWI本文は文章lintの必須対象から除く。口語表現とダッシュは保存前の診断が行番号付き警告で表示し、処置は起草者が判断する（保存の可否は構造エラーの有無で決まる）。計画とWI本文から転記する文面は転記先の基準でチェックする

## 口語表現チェック

MCPの`run`を利用できない場合だけ、`agent-toolkit:check-execution`の「pyfltrの起動形」で解決したCLIで同じ対象と制約を指定する（外部ファイルでは`--allow-external-paths`を加える）。完了はJSONL最終行の`summary`レコードの`completion`、`files_reached`、`completed_commands`、`incomplete_commands`でMCPと同じ条件で判定する。

## ダッシュチェック

日本語の地の文・見出しにおけるemダッシュ・horizontal bar・2倍ダッシュは`atk run-script writing-check-dash -- <ファイル>`で検出する。コードブロック・インラインコード・URL内は対象外とし、詳細は`agent-toolkit/skills/writing-standards/scripts/check_dash.py`が定める。

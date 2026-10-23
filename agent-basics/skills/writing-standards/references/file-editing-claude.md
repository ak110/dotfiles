# Claude Codeの既存ファイル編集

Claude CodeのEditと、Writeによる既存ファイルの上書きに適用する。共通の変更・結果確認は`file-editing.md`が定める。

## 名指し参照の読込方法

名指し参照は`../SKILL.md`「名指し参照の読込方法」に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文の共通編集基準を使う時 | `file-editing.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/file-editing.md` |

## 操作前の読取状態

Editと既存ファイルへのWriteは、対象パスの読取状態をホストが保持していることを要求する。編集前に同じ会話で対象の絶対パスをReadし、その状態を成立させる。本文の理解とツールが要求する読取状態は別に確認する。

次の取得や操作はReadの代わりにしない。

- Bashでの表示。Read状態になるかはコマンド解析に依存し、変数を含むパスや連結したコマンドでは成立しない
- リダイレクト、`cp`、`sed -i`、`iconv`などによる作成・変更
- 別パスのRead、他の担当から受け取った本文、以前のWrite結果

複数のEditをまとめる場合は各対象のRead成功後に発行する。未読で拒否された編集は、対象をReadしてから再発行する。軽量書込担当にも同じ操作前条件を適用する。

監査記録は`docs/development/audit-records.md`の「agent-basics/skills/writing-standards/references/file-editing.md：ファイル更新時の編集ツールの前提：2026年10月7日」にある。

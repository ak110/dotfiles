# 再発防止観点の配置と収集

品質基準の定義元は`agent-basics:bugfix`の`references/recurrence-self-check.md`である。呼び出し元の取得条件と注目箇所を引き継ぎ、本書と正式な資料を全文読む。節指定がある場合は、指定された見出しを持つ資料に注目する。両方に同名の見出しがあれば両方に注目する。すでに取得済みの同一バージョンの本文がある場合は、再取得せずにそれを再利用する。

agent-toolkitにおけるチェック観点は、`${CLAUDE_PLUGIN_ROOT}/share/recurrence-self-check-items.md`に配置する。観点ファイルのH1見出しは`# 再発防止セルフチェック観点`で開始し、この見出し宣言は観点ファイルにのみ使用する。ファイルを分割した先のファイルも同一のH1宣言を持つ。実行レビューにおける観点の収集は、配置されているディレクトリにかかわらず同一の手順で実施する。

## 名指し参照の読込方法

名指し参照は`../SKILL.md`「名指し参照の読込方法」に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:bugfix/references/recurrence-self-check.md` | 判断 | `read:agent-basics:*/skills/bugfix/references/recurrence-self-check.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../../share/recurrence-self-check-items.md` | 判断 | `read:agent-toolkit:*/share/recurrence-self-check-items.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:bugfix/references/recurrence-self-check.md#「観点の書き方」` | 判断 | `section:agent-basics:*/skills/bugfix/references/recurrence-self-check.md#「観点の書き方」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:bugfix/references/recurrence-self-check.md#「ファイルの配置と分割」` | 判断 | `section:agent-basics:*/skills/bugfix/references/recurrence-self-check.md#「ファイルの配置と分割」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:bugfix/references/recurrence-self-check.md#「観点の削除・整理」` | 判断 | `section:agent-basics:*/skills/bugfix/references/recurrence-self-check.md#「観点の削除・整理」` |

## 書き方

品質基準の詳細は、`agent-basics:bugfix`の`references/recurrence-self-check.md`「観点の書き方」を参照して適用する。

## 配置と分割

品質基準の詳細は、`agent-basics:bugfix`の`references/recurrence-self-check.md`「ファイルの配置と分割」を参照して適用する。

## 除去

品質基準の詳細は、`agent-basics:bugfix`の`references/recurrence-self-check.md`「観点の削除・整理」を参照して適用する。

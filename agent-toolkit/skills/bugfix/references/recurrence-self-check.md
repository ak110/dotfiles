# 再発防止観点の配置と収集

品質の定義元は`agent-basics:bugfix`の`references/recurrence-self-check.md`である。呼出元が指定した条件と読込範囲を、同名の正式な品質資料へ引き継ぐ。節指定がある場合は移設先の実見出しへ接続し、指定がない場合は従来の全文読込契約を保つ。取得済みの同版本文は再取得せず使う。

agent-toolkitの観点は`${CLAUDE_PLUGIN_ROOT}/share/recurrence-self-check-items.md`へ置く。観点ファイルのH1は`# 再発防止セルフチェック観点`で始め、この宣言は観点ファイルだけで使う。分割先も同じH1宣言を持つ。実行レビューの収集は配置ディレクトリによらず同じ手順で行う。

## 名指し参照の読込方法

本文の各参照の適用条件を保ち、選んだ資料の指定範囲を取得してから、その基準を使う具体的な判断へ進む。起動時の行の資料は起動本文とともに届く。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:bugfix/references/recurrence-self-check.md` | 判断 | `read:agent-basics:*/skills/bugfix/references/recurrence-self-check.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../../share/recurrence-self-check-items.md` | 判断 | `read:agent-toolkit:*/share/recurrence-self-check-items.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:bugfix/references/recurrence-self-check.md#「書き方」` | 判断 | `section:agent-basics:*/skills/bugfix/references/recurrence-self-check.md#「書き方」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:bugfix/references/recurrence-self-check.md#「配置と分割」` | 判断 | `section:agent-basics:*/skills/bugfix/references/recurrence-self-check.md#「配置と分割」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:bugfix/references/recurrence-self-check.md#「除去」` | 判断 | `section:agent-basics:*/skills/bugfix/references/recurrence-self-check.md#「除去」` |

## 書き方

品質の定義は`agent-basics:bugfix`の`references/recurrence-self-check.md`「書き方」を適用する。

## 配置と分割

品質の定義は`agent-basics:bugfix`の`references/recurrence-self-check.md`「配置と分割」を適用する。

## 除去

品質の定義は`agent-basics:bugfix`の`references/recurrence-self-check.md`「除去」を適用する。

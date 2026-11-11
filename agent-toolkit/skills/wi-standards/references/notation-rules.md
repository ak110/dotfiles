# 表記とMarkdownの規則と確認手段

執筆規則の正式な提供元は`agent-basics:writing-standards/references/notation-rules.md`である。
チェック対象・実行の正式な提供元は`agent-basics:writing-standards/references/notation-checks.md`である。
呼び出し元の読込条件を引き継ぎ、本書の運用条件と条件に該当する正式な資料の全文を読む。
両方に同じ主題があれば、両方の全文を確認する。
範囲の選択と取得済み本文の再利用は`agent-basics:writing-standards/references/read-method.md`に従う。

以下はagent-toolkit固有の運用条件である。

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 |
| --- | --- |
| 文章を執筆・修正する前 | `agent-basics:writing-standards/references/notation-rules.md` |
| チェックの対象・実行と診断を判断する前 | `agent-basics:writing-standards/references/notation-checks.md` |

## チェックの対象範囲

- `atk wi add`や`atk wi edit`の`--body-file`引数へ渡すWI本文は、文章lintの必須チェック対象から除外する。
  口語・ダッシュなど意味を変えない表現の診断や推敲は行わず、意味と構造を検収する。
  WIや計画からコード・文書・設定などへ転記する時点で、要求の目的と意味を保って転記先の品質基準と妥当なlint診断へ従う。
  WIの説明・例示の文面に従ったことを理由に、成果物の不良を据え置かない。
  要件・認可と、明示的に逐語性を求められた公開文言は、説明・例示の表現と区別して保持する。

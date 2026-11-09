# 実行レビューの指摘と完成条件証拠の記録：review_contractの書式

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 未コミット検証の結果を適用判定する前 | `verification-evidence-observation.md` | 判断 | `read:agent-toolkit:*/share/verification-evidence-observation.md` |
| 返却形式を確認する時 | `exec-review.parent.md` | 判断 | `read:agent-toolkit:*/share/exec-review.parent.md` |
| AWIの形式を判断する前 | `../skills/wi-standards/references/awi-body.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/references/awi-body.md` |
| 差分外の候補を実在する不良として分類する前 | `agent-basics:writing-standards/references/investigation-sources.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/investigation-sources.md` |
| 保存済みの成功集合と限定再実行から被覆を判定する前 | `agent-basics:check-execution/references/verification-scope-reruns.md` | 判断 | `read:agent-basics:*/skills/check-execution/references/verification-scope-reruns.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-toolkit:wi-standards/references/origin-and-approval.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/references/origin-and-approval.md` |
| 検証の実行範囲を判断する前 | `agent-toolkit:/share/exec-review-operations.md` | 判断 | `read:agent-toolkit:*/share/exec-review-operations.md` |

## review_contractの書式

一般条項と判定対象外commitは次の形式で記載する。
新形式のエージェント由来AWIの条件表は「記録する行と要求単位」に従う。

```markdown
## 条項1

- 条項: <対象と判定内容>
- 内容: <契約本文、または参照: <絶対パス>#<見出し>>
- 出典: <明示入力の出所>

## 判定対象外commit

- <完全OID>: <認可した基準と認可根拠の絶対パス、認可された対象範囲>
```

## 記録する行と要求単位

新形式のエージェント由来AWIの`review_contract`には次の条件表を設ける。
起動元は、`## 反映内容と反映先`の最上位の各リスト項目から1件以上ずつ主要完成状態を作る。
リストが無い場合は最上位の各段落を使い、項目・段落の出現順を1から番号付けする。
各行の出典にWI名と`反映内容N`（Nはその番号）を記し、項目が指定するファイル、規定の内容、保持する値など具体的な成果を条件から省かない。
目的と原因分析から導く状態は対応する項目の行へ含めるか別行とし、背景説明や他項目への割当など完成判定に使わない項目も、同じ出典を持つ行に除外理由を記す。
除外理由の行には`主要完成状態`と一意な条件番号を付け、除外理由が原文と採否記録に一致することをその行の条件とする。
レビュー担当は理由の一致を判定し、割当先の成果自体の完了はこの行の判定対象から除く。
補足条件は`## 完成条件`の最上位リスト項目から取り出し、リストが無ければ最上位の各段落を使う。
主要完成状態と区別して記入し、条件番号はWIごとに両区分を通じて一意にする。
起動元は起動前に列、番号の一意性と全反映項目の出典への対応を確認する。
レビュー担当は出典の項目ごとに原文と条件の内容を読み比べ、具体的な成果の欠落、表の欠落、不足や誤りを判定行の作成前に起動元へ差し戻す。
完成条件行の作成と統合時の条件比較には、検収した同じ表を用いる。

| WI | 条件番号 | 区分 | 内容 | 出典 |
| --- | --- | --- | --- | --- |
| <WI名> | <番号> | 主要完成状態・補足条件のいずれか | <条件> | <WIと箇所> |

- 完成条件の節がある旧形式のエージェント由来AWIと人間由来AWIについては、各条件を完成条件行に記録し、同じAWIの末尾にある`## ユーザーコメント`から切り出した要求単位を原文要求行に記録する
- 新形式のエージェント由来AWIでは、本節の項目ごとの主要完成状態と補足条件を完成条件行に記録し、本文を原文要求として扱わない。
  既存の番号付き完成条件はその番号を保ち、主要完成状態には既存番号と重ならない番号を付ける。
  レビュー担当とレーン担当は同じ契約を使用する。
  同じAWIの`## ユーザーコメント`から切り出した要求単位は原文要求行に記録する
- 完成条件節のない人間由来の原文AWIの場合は、frontmatterと処理結果を除いた本文から要求単位を抽出する。
  回答済みのUWIについては、`## 回答`に記入された本文から要求単位を抽出する（回答欄に記載された案内コメント、質問文、選択肢、判断材料、処理結果は除外する）。
  計画ファイルの`ユーザー指示`行についても同様に記録する
- `## ユーザー指摘の逐語引用`は、投入元のセッションで解決済みの発話であるため（`agent-toolkit:wi-standards`の`references/origin-and-approval.md`）、ここからは要求単位を抽出せず、解釈の背景根拠として読み取る
- 要求単位の最小単位は、箇条書きの各項目、句点（`。`）・疑問符（`?`）・感嘆符（`!`）で区切られた文、または終止記号のない段落とする。
  ASCII文字の`.`、`!`、`?`については、直後に空白があるか段落末の場合のみ文末とみなす（ドメイン名、ファイル名、バージョン番号、インラインコード内にあるものは文の一部として扱う）。
  直後に閉じ括弧がある場合は同じ文に含める。
  ユーザー確認の回答記録（行頭に`質問:`があり、その後に`回答:`がある形式）では、質問以前の地の文、および回答・自由記述の内容のみを要求単位とする。
  補足として添えられたコードブロック（```）は単位に分割せず、背景資料として参照する

# 指摘の受領

## ユーザー指摘の受領

指摘の採否や修正範囲を確定する前に、現行の作業契約と要求の原本を確認し、維持すべき対象、全体への波及影響、および元々の未完了タスクを把握する。作業契約の凍結後に行われた変更や指摘の採否は、レビュー指摘管理表へ記録する。
作業契約への反映手順は`${CLAUDE_PLUGIN_ROOT}/share/work-contract.md`「更新する時点」に従う。`review_contract`の保持と再生成は、`${CLAUDE_PLUGIN_ROOT}/share/exec-review-contract-generation.md`「`review_contract`の生成」に従う。

- 指摘の対象が、自動処理待ちの未完了な既存WIである場合は、事実確認する前に`agent-toolkit:wi-standards`の`references/states-and-dependencies.md`に従って対象を「保留」状態にして保護する。この保留は、要求に同意したことや本文を変更したことを意味するのではなく、内容を比較検討している間に古い要求に基づいて処理が進んでしまうのを防ぐための処置である
- 現物と突き合わせた結果がユーザーの発言と一致しない場合は、同意や修正より先に根拠と代替案を説明する
- 解釈の候補を現物と比べても2つ以上残る場合は、ユーザーへ直接意図を確認する。修正対象と修正内容が一意に決まる事項は確認せずに是正し、確認結果と裏付け手段を同じ応答に記載する。ユーザー自身の判断で修正内容が変わる事項や、追加の認可が必要な操作は、着手前にユーザーへ確認する
- 指摘を受けても、自律モードで残っている作業の実行義務は取り消されない。自身の提案に誤りが見つかったときは、提案の取り下げだけで終わらせず、観測した事象を起点にして代替案を提示する

## 参照資料の取得

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-toolkit:/share/work-contract.md` | 判断 | `read:agent-toolkit:*/share/work-contract.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-toolkit:/share/exec-review.parent.md` | 判断 | `read:agent-toolkit:*/share/exec-review.parent.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-toolkit:/share/exec-review-contract-generation.md` | 判断 | `read:agent-toolkit:*/share/exec-review-contract-generation.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-toolkit:wi-standards/references/states-and-dependencies.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/references/states-and-dependencies.md` |

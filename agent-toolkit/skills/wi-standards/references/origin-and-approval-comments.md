# 由来と承認：ユーザー専用の記入欄と是正要求

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 |
| --- | --- |
| 手動process-wiの公開承認を判定する前 | `../../process-wi/references/lanes-completion.md` |

## ユーザー専用の記入欄と是正要求

UWIの`## 回答`およびAWIの`## ユーザーコメント`は、ユーザーのみが編集できる節である。
エージェント環境から実行された`atk`は、これらの節を含む本文の投入や編集を拒否し、`atk wi edit --body-file`では`## ユーザーコメント`を編集対象から除外して保存済みの内容を保護する。
エージェント由来の項目に記載されたユーザーコメントや回答が、その項目の前提、対象、方針、採否を否定している場合は、ユーザーからの是正要求として扱う。
これを受け取ったエージェントは、対象項目が未終了であれば先に`states-and-dependencies-editing.md`「保留と本文修正」に沿って保留（hold）する。
その上で`agent-basics:review-standards/references/reviewee-acceptance.md`「ユーザー指摘の受領」に従って採否を確定する。
続いて`agent-basics:bugfix/references/root-cause-analysis-initial.md`「問題を見つけたときの対処」に従い、類似見直しと再発防止策の策定まで完了させる。
本文の書き換えは個別事象への対症療法にすぎないため、処置の階層（対策、回帰防止策、横展開処置、再発防止策）を同スキルの`agent-basics:bugfix/references/root-cause-actions-hierarchy.md`「処置の階層」に基づいて明確に区別する。
記入内容が以後の同種作業にも適用する方針の候補を含むかは、是正要求に当たるかによらず、`../../user-confirmation-and-report/references/user-utterance-incorporation.md`「原文の保持と取り込み」で判定する。
ユーザーがこれらの記入欄に処理担当宛ての質問を記載した場合は、その回答をUWIに記録して返却し、その保存完了を、対象の維持・変更を確定して元のWIを終了させるための必須条件とする。
既存のWI（ステータスは問わない）を別の判断における前提、制約、代替案の不採用理由、完成条件などの根拠として参照する場合も、ユーザー専用記入欄を含めた全文を精読する。
`origin-and-approval-origin.md`「由来と`source`」に定める要求単位ごとの明示的な由来から有効な要求を確定して使用する。
エージェント由来の本文記述とユーザーコメントや回答の内容が衝突する場合は、要求単位の由来と`agent-toolkit/share/rules/common/01-agent.md`「方針が衝突する場合の優先順位」に基づいて有効な前提を決定する。
ユーザーによって是正された本文側の古い記述は根拠から除外する。
根拠として参照する判断の主体が、その項目の是正要求を直接受け取ったエージェント自身でない場合も同様である。

## 本文参照の取得条件

| 時点または条件 | 全文読む資料 |
| --- | --- |
| 初動と直接的原因の確認：問題を見つけたときの対処の基準を適用する前 | `agent-basics:bugfix/references/root-cause-analysis-initial.md` |
| レビューイー側心得：レビュー指摘の位置付けの基準を適用する前 | `agent-basics:review-standards/references/reviewee-acceptance.md` |
| 由来と承認：由来と`source`の基準を適用する前 | `origin-and-approval-origin.md` |

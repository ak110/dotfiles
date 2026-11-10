# レビューイー側心得：同じ指摘の反復

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 指摘前の判断記録と比較する前 | `exec-review-recording.md` | 判断 | `read:agent-basics:*/skills/review-standards/references/exec-review-recording.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../bugfix/references/root-cause-prevention-requirements.md` | 判断 | `read:agent-basics:*/skills/bugfix/references/root-cause-prevention-requirements.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:bugfix/references/root-cause-remediation.md` | 判断 | `read:agent-basics:*/skills/bugfix/references/root-cause-remediation.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../bugfix/SKILL.md` | 判断 | `read:agent-basics:*/skills/bugfix/SKILL.md` |
| 調査の停滞や修正による症状・回避処理の増加に対処する前 | `../../bugfix/references/root-cause-scenarios-recurrence.md` | 判断 | `read:agent-basics:*/skills/bugfix/references/root-cause-scenarios-recurrence.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../bugfix/references/root-cause-scenarios-recurrence.md`、`../../bugfix/references/root-cause-remediation.md`、`../../bugfix/references/root-cause-prevention-requirements.md` | 判断 | `read:agent-basics:*/skills/bugfix/references/root-cause-scenarios-recurrence.md`、`read:agent-basics:*/skills/bugfix/references/root-cause-remediation.md`、`read:agent-basics:*/skills/bugfix/references/root-cause-prevention-requirements.md` |

## 同じ指摘の反復

**4.** 反復の原因に対処するため、再レビューの指摘なら修正方針の決定前に全ラウンドの指摘と応答を読み返し、今回の違反規定・発生条件と比べる。
   完了条件の一致や指摘件数だけで安易に同一性を判断せず、修正の不足、新しい要件の発生、レビュー契約自体の不足を切り分けて整理する。

   同じ論点で修正が繰り返されている場合は、指摘内容と現物を突き合わせて、ユーザーの意図と要件への影響を確認する。
   ユーザー自身の判断によって意図や変更範囲が分かれる場合は、その判断が確定してから変更する。

   違反した規定と発生条件の組み合わせが同一である指摘（文言、対象ファイル、個別箇所の違いは問わない）を再度受け取ったときは、その場しのぎの局所修正を止め、`agent-basics:bugfix`を起動する。
   同スキルの初動手順と`../../bugfix/references/root-cause-scenarios-recurrence.md`「反復した指摘の原因判定」へ引き渡す。
   調査が停滞した場合や、修正によって別の症状・回避処理が増えた場合も、同資料「同一症状の修正反復」に従って調べ方を見直す。
   取得済みの診断・失敗記録と棄却した前提を再利用し、難航時の報告後も認可内の技術調査を続ける。
   共通判定には`agent-basics:bugfix/references/root-cause-remediation.md`「類似見直し」も適用する。
   `../../bugfix/references/root-cause-prevention-requirements.md`「再発防止策の必須性」を併用する。
   比較の根拠と処置の採否を応答に記録する。
   一般的な設計案の比較は`../../writing-standards/references/design-heuristics-proportionality.md`「問題と手段の比例性」に従い、現在の設計を維持する場合はその理由を明記する。
   欠陥を解消し、かつ新たな問題を引き起こさない案を立証できないときは、検討した案と理由を委譲元へ報告する。

# 待機の異常時の扱い：待機を開始できない場合

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 |
| --- | --- |
| 待機の呼び出しが拒否された時、またはclassifierが判定不能を返した時 | `../../user-confirmation-and-report/references/permission-denial.md` |
| 必須操作を報告する前 | `../../user-confirmation-and-report/SKILL.md` |

## 待機を開始できない場合

1. 起動失敗と拒否を区別するため、待機が未起動の場合は`../../user-confirmation-and-report/references/permission-denial.md`「共通判断」で通知本文の再試行指示を確認する。
   判定は通知本文の再試行指示に基づく。
   - `gave no verdict (error)`で「transient failure」「You may try the action again once」と示す通知は、許された1回の再試行と指定の遅延に従う。
   - `gave no verdict (it skipped this action)`で「hard failure」「Don't retry it」と示す通知は、同じ待機の起動を再試行しない。
     同じ変更を別ツールで改めて判定させることや権限設定の変更も行わない。
     読み取りや独立作業と、許容された別の観測手段による終了観測を続ける。
     その操作が必須なら、対象・操作と通知本文を、委譲先は`rules-subagent.md`「確認事項の即時通知」で委譲元へ、メインは`agent-toolkit:user-confirmation-and-report`「手段の選択」でユーザーへ伝える。
   実際の拒否は同書の拒否主体別の節に従う。

2. 観測を引き継げるよう、失敗した待機の起動状態、既存の待機の実行識別子、未回収の子や背景処理の識別子を保持する。
   元のエラーが許す同じ操作の再試行と指定の遅延を適用し、ホストの回数・turn停止制約を守る。
   再試行までの間は独立作業を進める。
   classifierへの再試行間隔を、子の完了を固定sleepで繰り返し照会する方式へ変えない。
3. 待機が未起動の間も、未回収対象の終了観測の責任は残る。
   既存の許容された待機・回収手段を使える間は、同じ対象の終了観測を継続する。
   自動再開の能力だけを根拠に終了せず、今回の対象の追跡・結果保留・継続が成立しているかを`waiting-and-monitoring-intervals.md`、`waiting-and-monitoring-auto-resume.md`で確認する。
4. 観測主体を確保するため、ホスト上限や停止制約により観測を継続できない場合は、未回収対象、待機の起動状態、診断と再開条件を委譲元へ渡し、観測を引き継ぐ主体を明確にする。
   未回収の対象は未完了として返す。

## 本文参照の取得条件

| 時点または条件 | 全文読む資料 |
| --- | --- |
| 待機と観測：完了通知を待ってターンを終える場合の基準を適用する前 | `waiting-and-monitoring-auto-resume.md` |
| 待機と観測：待機区間の構成の基準を適用する前 | `waiting-and-monitoring-intervals.md` |

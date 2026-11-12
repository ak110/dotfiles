# 権限拒否への対応：一時的な判定不能

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 |
| --- | --- |
| 判定不能により待機を開始できない時 | `../../delegation/references/wait-anomalies-refusal.md` |

## 一時的な判定不能

1. classifierの判定不能は、通知本文の再試行指示で区別する。
   `no verdict`という語だけで一時的な失敗と扱わず、操作への拒否・`hard_deny`・偽陽性とも分ける。
2. `gave no verdict (error)`で`transient failure`・`You may try the action again once`と示す一時的な失敗では、許された同じ操作の再試行を1回だけ選ぶ。
   通知が指定する遅延、再試行回数、turn停止の制約を守る。
3. `gave no verdict (it skipped this action)`で`hard failure`・`Don't retry it`と示す操作は、同じ操作を再試行しない。
   読み取りや独立作業を続け、その操作が必要なら対象・操作・通知本文を伝える。
   委譲先は`agent-toolkit/share/rules/rules-subagent.md`「確認事項の即時通知」で委譲元へ、メインは本スキル「手段の選択」でユーザーへ伝える。
   同じ変更を別ツールで実行して再判定させることや、権限設定の変更は対処に含めない。
4. 操作が未起動・実行中・完了・成否不明のどれかと未回収対象の識別子を保持する。
   権限設定の変更や別手段による保護回避は行わない。
   待機を開始できない場合の観測継続は`../../delegation/references/wait-anomalies-refusal.md`「待機を開始できない場合」に従う。

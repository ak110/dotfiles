# Codexでの計画工程

本書はCodexで計画を起草するメインへ、起草へ進む条件とPlan modeの扱いのCodex固有の差分を定める。

## 名指し参照の読込方法

名指し参照は`../SKILL.md`「名指し参照の読込方法」に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../user-confirmation-and-report/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/user-confirmation-and-report/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../user-confirmation-and-report/SKILL.md#「問い・提案・疑問形の扱い」` | 判断 | `section:agent-toolkit:*/skills/user-confirmation-and-report/SKILL.md#「問い・提案・疑問形の扱い」` |

## 計画ファイルの起草開始条件

初版はネイティブPlan modeの有無を問わず、関連調査を終えて未確定事項がなくなってから起草する。外部仕様と実行時挙動は公式一次資料か実機で確かめる。ユーザーだけが決められる事項は`agent-toolkit:user-confirmation-and-report`の確認を終えてから起草する（提案的表現の扱いは`agent-toolkit:user-confirmation-and-report`の`SKILL.md`「問い・提案・疑問形の扱い」）。自律モードで回答を同期的に得られない場合は、常時規範のUWI記録と暫定判断を終えた事項だけを起草の対象にできる。
`update_plan`で「調査」と「計画ファイル起草」を分け、調査を`completed`にするときに`explanation`へ確認対象・手段・確定事項・未確定事項の有無を書くことを推奨する。
起草後に事実不足が判明したら調査へ戻る。レビュー指摘の反映と進捗ログの追記はこの開始条件の対象外とする。

## Plan modeの扱い

Plan modeはターン開始時点のホスト状態で扱い、切替はホスト側の操作でだけ起こるものとする。

- ネイティブPlan modeが有効なら、ホストのガードレール、Plan限定対話、承認後の遷移を使う。ファイル変更を遮断する範囲はホストの公開仕様だけを根拠に述べる
- 有効でなければ、計画ファイル以外を変えない運用と開始・終了宣言を互換運用として適用する
- どちらでも計画に必要な読取は実行できる。ネイティブモードの提供範囲と互換運用の範囲は書き分ける

# レーン計画の起草と再開：作成要否と計画なしの準備

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 複数リポジトリの共通本文を参照するAWIを読む前 | `../../wi-standards/references/cross-repository-submission.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/references/cross-repository-submission.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../../share/exec-subagent-implementation.md` | 判断 | `read:agent-toolkit:*/share/exec-subagent-implementation.md` |
| 旧形式WIの節を現行の責務へ対応付ける前 | `../../wi-standards/references/awi-body.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/references/awi-body.md` |

## 作成要否と計画なしの準備

**1.** 計画省略の条件を満たす全AWIについて、`atk wi show`で要求と完成条件を確認する。

**2.** 実装と統合の根拠として、由来・採否、AWI集合、最も近い既存実装、直接影響範囲、変更範囲の検証コマンド、統合区分（実装する場合は`マージあり`、不要なら`マージなし`）を引き継ぎ記録先へ記録する。

**3.** 明示された禁止条件があれば、禁止条件と採用する手段も記録し、次の形式のみを返してメインの`実装開始`または`実装なし`を待つ。

```text
計画なし準備完了
AWI: <AWIファイル名のASCIIカンマ区切り>
統合区分: <マージあり|マージなし>
```

**4.** 禁止条件がなければ、`lane-planning.md`「計画検査完了の通知」を`計画ファイル: なし`として送り、応答を待たずに`../../../share/exec-subagent-implementation.md`「実装」へ進む（`マージなし`では統合指示を待つ）。
設計判断、バグ対応、人間由来の要求の縮小が必要と判明したら`lane-planning-drafting.md`「計画の起草」へ戻る。

## 本文参照の取得条件

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| レーン計画の起草と再開：計画の起草の基準を適用する前 | `lane-planning-drafting.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/references/lane-planning-drafting.md` |
| レーン計画の起草と再開の基準を適用する前 | `lane-planning.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/references/lane-planning.md` |

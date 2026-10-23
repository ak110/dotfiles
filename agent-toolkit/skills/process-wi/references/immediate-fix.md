# 即時対応の手順と対象外の扱い

本書は`agent-toolkit:process-wi`のメインが、不良またはユーザーの是正要求に同スキルの`SKILL.md`「即時対応」の判定基準を当てはめた後の手順を定める。

## 名指し参照の読込方法

名指し参照は`../SKILL.md`「名指し参照の読込方法」に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../SKILL.md#「即時対応」` | 判断 | `section:agent-toolkit:*/skills/process-wi/SKILL.md#「即時対応」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../../share/exec-review.parent.md#「返却に含まれる既存不良の受領」` | 判断 | `section:agent-toolkit:*/share/exec-review.parent.md#「返却に含まれる既存不良の受領」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../../share/add-wi.parent.md` | 判断 | `read:agent-toolkit:*/share/add-wi.parent.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../wi-standards/SKILL.md#冒頭` | 判断 | `section:agent-toolkit:*/skills/wi-standards/SKILL.md#冒頭` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../session-review/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/session-review/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `selection-procedure.md#「処理対象WIの追加」` | 判断 | `section:agent-toolkit:*/skills/process-wi/references/selection-procedure.md#「処理対象WIの追加」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `run-lanes.md#「処理中に確定した必須是正レーン」` | 判断 | `section:agent-toolkit:*/skills/process-wi/references/run-lanes.md#「処理中に確定した必須是正レーン」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../bugfix/references/root-cause-analysis.md#「類似見直し」` | 判断 | `section:agent-toolkit:*/skills/bugfix/references/root-cause-analysis.md#「類似見直し」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../../share/exec-review.parent.md#%E3%80%8C%60review_contract%60%E3%81%AE%E7%94%9F%E6%88%90%E3%80%8D` | 判断 | `section:agent-toolkit:*/share/exec-review.parent.md#%E3%80%8C%60review_contract%60%E3%81%AE%E7%94%9F%E6%88%90%E3%80%8D` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../bugfix/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/bugfix/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../wi-standards/SKILL.md#「由来と承認」` | 判断 | `section:agent-toolkit:*/skills/wi-standards/SKILL.md#「由来と承認」` |

## 即時対応の手順

本手順はメインまたは委譲先が発見した既存不良と、処理中のユーザーの是正要求に適用する。実行レビューの差分外不良は通常`${CLAUDE_PLUGIN_ROOT}/share/exec-review.parent.md`「返却に含まれる既存不良の受領」でAWIへ渡し、進行に必要な直接修正がある場合は本手順を使う。
即時対応は最小直接修正と正式対応AWIの登録までとし、独立した既存不良を同じセッションで正式対応まで完了する一般則に対する例外とする。

1. 観測事象、期待結果、現在または次のセッションへの作用を確かめ、進行に必要な直接修正の対象と方向を決める。安全に修正できるだけの原因を確かめ、本格的な原因分析は正式対応へ残す。
2. メインまたは書込を所有する担当が必要な範囲だけ修正し、変更箇所の安全な利用を確認する。対象・方向・認可・観測結果を既存の引き継ぎ記録へ残す。
3. `${CLAUDE_PLUGIN_ROOT}/share/add-wi.parent.md`で正式対応AWIを起草・投入する。暫定修正のcommitまたは差分、観測と検証結果、未確認事項、残るテスト・独立文脈レビュー・原因分析・類似見直し・再発防止を渡す。既存AWIを再利用する場合は、この全範囲を保持することを確かめる。登録したAWIは今回の選定へ追加せず、次のセッションへ渡す。
4. 直接修正と正式対応AWIの対応を引き継ぎ記録へ残して元の工程を続ける。ユーザーの明示追加と本作業で導入した不良は、元の要求と完成条件に従って扱う。

正式対応を取得した次のセッションでは、暫定修正を完成扱いにせず、AWIの全要求と`agent-toolkit:bugfix`の基準で検証する。

## レーンの変更範囲へ加える修正の認可

レーン担当が進行を妨げる既存不良を報告し、直接修正の対象と方向が技術的に定まる場合は、その最小修正を同じレーンの変更範囲へ加える認可を同じthreadへ追送する。修正のために正式対応AWIの起草完了を待つ必要はない。

- 認可には修正の対象、方向、出所（レーン担当の報告とメインの判定）を含める。この対象と方向が加える修正の書込の認可の境界であり、エージェント向け文書も別に名指しせず同じ境界で扱う。凍結済みの計画は更新しない
- 実行レビュー担当の起動時に、その認可を`review_contract`の条項として渡す（`${CLAUDE_PLUGIN_ROOT}/share/exec-review.parent.md`「`review_contract`の生成」）
- 対象か方向が定まらない場合は安全な修正に必要な事項を調べ、ユーザーだけが決める値が残る場合は確認する
- 修正済みの箇所も正式対応AWIの検証対象へ含める。メインが修正のcommitと報告を投入担当へ渡し、正式対応の登録を確かめる

## 対象外の不良と是正要求

即時対応の対象外とした不良と是正要求、および公開工程の開始後に見つけた公開を妨げない不良と新しい作業要求は、レーンへ割り当てず`add-wi.parent.md`の委譲で`source: process-wi`のAWIとして投入し、次のセッションへ回す。ユーザーの要求は`agent-toolkit:wi-standards`「由来と承認」に従って発話を逐語で転記する。
メインは即時対応の対象外とした既存不良についても、投入担当の結果を受領した時点で投入済みWIと対象を自身の引き継ぎ記録へ対応付け、報告元へその処置を返す。
公開工程の開始後でも、公開を妨げる不良（CI失敗など）は終端担当の手順で直す。独立した既存不良への最小修正は正式対応AWIへ対応付ける。ユーザーが同じ実行での実施を明示した要求と本作業で導入した不良は、元の要求と完成条件に従って同じセッションで扱う。

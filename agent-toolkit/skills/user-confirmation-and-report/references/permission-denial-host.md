# 権限拒否への対応：writer境界・Codexホストの拒否

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 判定不能により待機を開始できない時 | `../../delegation/references/wait-anomalies-refusal.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/wait-anomalies-refusal.md` |

## writer境界・Codexホストの拒否

agents_serverのwriter境界では、割り当てられた操作範囲と書込所有権を保持し、拒否された照会・操作を権限のある委譲元へ返す。
Codexホストの権限拒否では、そのホストの診断と公開された承認手段に従う。
両者の拒否だけを根拠にClaude Codeの設定照会や偽陽性承認へ進まない。

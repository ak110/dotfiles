# メインによるWI選定とレーン分け

対象リポジトリのWIキューから今回の処理対象・依存順・レーン割当と、前回`processing`に残った項目の再開位置を確定する。
回答済みUWIを処理し、新しく選定したAWIを`processing`へ移す。
計画起草・実装・`reject`・AWIの`adopt`は後続担当が行う。
必要な監査を起動後、独立した既存候補を選定してレーンを起動する。
結果が必要な候補だけ待ち、追加WIは正式名の受領後に`selection-procedure-addition.md`「処理対象WIの追加」で合流する。
`all-repos`のリポジトリ担当は受け取った取得済み候補を使い、通常起動では`atk wi list`で候補を取得する。

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

## 読込表

| 時点または条件 | 全文読む資料 |
| --- | --- |
| 処理対象WIについて、包含、除外または構成の理由をユーザーが尋ねたとき | `selection-explanation.md` |
| 回答済みUWIが元項目を指し、同じ元項目を指す未回答のactiveなUWIが残る時と、包含する候補へ依存を設定するかを判定する時 | `agent-toolkit:wi-standards`の`references/states-and-dependencies.md` |

## 条件別資料

| 時点または条件 | 全文読む資料 |
| --- | --- |
| 対象と保存先に関する判断・作業の前 | `selection-procedure-setup.md` |
| 処理対象の決定に関する判断・作業の前 | `selection-procedure-candidates.md` |
| 調査とレーン分け・反映先と上流投入・プロジェクト規範の指定に関する判断・作業の前 | `selection-procedure-allocation.md` |
| 集合確定直後の処理開始に関する判断・作業の前 | `selection-procedure-starting.md` |
| 保存後のチェック・保存後の意味検収・選定工程の完了に関する判断・作業の前（操作対象外: 自身の選定結果の検収） | `selection-procedure-validation.md` |
| 処理対象WIの追加に関する判断・作業の前 | `selection-procedure-addition.md` |

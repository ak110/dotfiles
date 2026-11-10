# 実行と状態変更の設計：キャッシュ設計

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 状態や業務ルールに関わる排他・トランザクションの単位を導く前 | `design-time-state.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/design-time-state.md` |
| 処理自体の失敗を異常終了で表すかを判断する前 | `../../bugfix/references/root-cause-prevention-selection.md` | 判断 | `read:agent-basics:*/skills/bugfix/references/root-cause-prevention-selection.md` |

## キャッシュ設計

発火条件: キャッシュ層を新規に導入または変更する場面。

保護すべき価値は、元データとの整合性が要件を満たす許容範囲内に収まること、およびキャッシュ層に障害が発生してもシステム全体の機能停止に直結しないことの2点である。
書き込み処理時におけるキャッシュ無効化の漏れ、キーの衝突、キャッシュ切れに伴う同時再生成処理の集中（キャッシュスタンピード）はいずれも上記2つの価値を損なう要因となるため、確実に防止策を講じる。

- 再利用効果: キーの変化頻度、期限の粒度、ヒット率、生成費用、容量・退避、再起動後の再構築
- 更新経路: 全件・差分取得、局所キャッシュ・複製、更新・削除・失効、応答前の反映範囲、部分失敗
- 時刻・期限の粒度や非同期反映は、有効期限・認可と成功応答で保証する可視性・耐久性を保つ範囲で選ぶ

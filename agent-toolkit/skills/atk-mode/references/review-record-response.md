# レビュー指摘管理表への応答

既存の行の応答本文と対応不要の理由は、`atk review-table show <表の絶対パス> --row-id <ROW_ID> --field <response|no-response-reason>`で読む（`row-id`は`show`の出力）。
応答の記録は、`atk review-table respond <表の絶対パス> --row-id <ROW_ID>`で行う。
対応する時は`--response-file <対応内容の絶対パス>`を、対応しない時は`--no-response-reason-file <理由の絶対パス>`を付ける。
互換の指定は`--round <ラウンド番号> --track <区分>`で、行が一意に定まらない時は`--location-file`と`--issue-file`も渡す。
表のセルはJSON文字列で保存されるので、互換の指定には`atk review-table show <表の絶対パス> --track <区分> --round <ラウンド番号> --format=jsonl`のデコード済みの値を渡す。

本文には、対応する時は最初の4つ、対応しない時は残りの2つのラベルを行頭に置き、同じ行に内容を書く。

- `違反を確認した規定:` 指摘が違反を示した規定、契約、要件とその所在
- `類似見直し完了件数:` 同じ原因が成立しうる箇所（`agent-basics:review-standards/references/reviewee.md`「安全な修正」手順8の、場合と読む側の組）のうち確かめた件数
- `採用する修正範囲:` 実際に直した箇所と、その範囲で違反が解消する理由
- `採用しない修正方針:` 指摘が提案した手段や検討した案のうち採らなかったものと理由（無ければ「なし」）
- `根拠の所在:` 不採用の根拠となる結論と記録か現物の再取得先、証拠が足りない時は足りない範囲と取得の手段（`agent-basics:review-standards/references/reviewee.md`「修正要否の立証」）
- `判断主体:` 不採用の判断を確定した主体

別のWIへ移管する時は、移管先を保存してから、任意の行頭ラベル`移管先:`に登録済みWIのファイル名を1件書く。

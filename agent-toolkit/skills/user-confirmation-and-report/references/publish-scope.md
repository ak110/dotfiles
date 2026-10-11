# 協調モードの公開範囲

本書は`agent-toolkit:user-confirmation-and-report`の細則として、協調モードで対象リポジトリの変更を伴う依頼へ着手する前に公開範囲を確認する手順を定める。

## 公開範囲の確認

協調モードで対象リポジトリの変更を伴う依頼では、変更に着手する前に依頼、既回答、恒常的認可と適用中の公開契約を調べ、`SKILL.md`「確認要否の判定」を適用する。同じ操作・対象・影響範囲・成立条件を覆う公開認可があれば再確認せず適用し、不足する認可かユーザーだけの未確定の選好が残る範囲だけを、ほかの確認事項と同じ質問ラウンドで問う。技術選択だけで公開認可を新設しない。

選択肢は第1案を推奨として次の順に示し、各選択肢の説明には`agent-toolkit:commit`の`references/publish.md`「公開範囲」の表がその範囲に定める「実施する工程」を使う。

1. 既存の判断基準どおり
2. push・CIまで
3. commitまで
4. commitしない

依頼文が公開範囲を指定している場合は、その指定を適用する。調査だけの依頼、成果物をWIへ限定する`agent-toolkit:add-awi-by-user`の起動中、自律モード（各スキルが定める公開工程を使う）は追加質問の対象外とする。
計画を作成する作業では回答を`## 要件・外部仕様`と`## 終端工程`へ記録する。後続の公開工程は`agent-toolkit:commit`の`references/publish.md`が定める。

## 終了工程の証拠への記録

Claude Codeの協調モードのメインは、同じ作業の公開範囲が指定・回答・適用済み認可から確定した時点で、原入力と判断結果を終了工程の証拠へ渡す。

1. `atk run-script termination-evidence -- --context`で現在の会話から原入力の`session_id`・`input_id`・`quote`を取得する。原入力を要約や推測した識別子へ替えない
2. JSONファイルへ`session_id`・`action: "publish-scope"`・`scope`・`origin`・`input_id`・原文全体の`quote`・判断理由の`reason`を記し、既存の作業があれば`work_id`も記す。`scope`は前掲の4選択肢の値で、`commitしない`も確定済みの範囲として渡す。`origin`は依頼文の指定なら`instruction`、回答なら`answer`、適用済み認可なら`standing-authorization`とする。適用済み認可では`policy_file`へ文書の絶対パス、`quote`へその現行本文全体、`input_id`へ認可を適用する作業の人間入力の識別子を渡す
3. `atk run-script termination-evidence -- --decision-file <JSONの絶対パス>`へ渡し、成功した記録の`work_id`を後続工程へ引き継ぐ

処理は原入力の由来と全文の一致だけを確かめ、意味判断と認可の適否はメインが確定する。既存作業への回答は同じ`work_id`へ対応付け、独立した新しい作業は既存の`start`判断で区切ってから公開範囲を渡す。作業をまだ作成していない場合だけ`work_id`を省略でき、原入力に属する作業が作成されて返る。

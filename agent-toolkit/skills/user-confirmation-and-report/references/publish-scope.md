# 協調モードの公開範囲

本書は、`agent-toolkit:user-confirmation-and-report`の詳細な規則として、協調モードにおいて対象リポジトリの変更を伴う依頼に着手する前に、公開範囲を確認する手順を定める。

## 名指し参照の読込方法

名指し参照は`../SKILL.md`「名指し参照の読込方法」に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../SKILL.md` | 判断 | `read:agent-toolkit:*/skills/user-confirmation-and-report/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../SKILL.md#「確認要否の判定」` | 判断 | `section:agent-toolkit:*/skills/user-confirmation-and-report/SKILL.md#「確認要否の判定」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../commit/references/publish.md#「公開範囲」` | 判断 | `section:agent-toolkit:*/skills/commit/references/publish.md#「公開範囲」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../add-awi-by-user/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/add-awi-by-user/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../commit/references/publish.md` | 判断 | `read:agent-toolkit:*/skills/commit/references/publish.md` |

## 公開範囲の確認

協調モードで対象リポジトリの変更を伴う依頼を処理する際は、実際の変更に着手する前に、依頼内容、過去の回答、恒常的な承認、および現在適用されている公開仕様を確認し、`SKILL.md`「確認要否の判定」を適用する。同一の操作、対象、影響範囲、成立条件をカバーする公開の承認がすでに存在する場合は、改めて確認せずそのまま適用する。承認が不足している範囲や、ユーザー固有の未確定な選好が残る範囲についてのみ、他の確認事項とまとめて同じ質問ラウンドで確認する。エージェントが技術的な選択のみを根拠として公開承認を勝手に新設することは禁止する。

選択肢は第1案を推奨案として次の順序で提示し、各選択肢の説明文には`agent-toolkit:commit`の`references/publish.md`「公開範囲」の表で定められている「実施する工程」を記載する。

1. 既存の判断基準どおり
2. push・CIまで
3. commitまで
4. commitしない

依頼文の中で公開範囲がすでに指定されている場合は、その指定を適用する。調査のみを目的とする依頼、作成する成果物がWIに限定される`agent-toolkit:add-awi-by-user`の実行中、および自律モード（各スキルが定める公開工程を使用する）では、追加の質問を省略する。
計画書を作成する作業では、確認結果を`## 要件・外部仕様`と`## 公開工程`に記録する。後続の公開工程の進め方は、`agent-toolkit:commit`の`references/publish.md`が定める。

## 終了工程の証拠への記録

Claude Codeの協調モードで動作するメインエージェントは、作業の公開範囲が指示、回答、または既存の承認から確定した時点で、元の入力情報と判断結果を終了工程の証拠記録へ渡す。

1. `atk run-script termination-evidence -- --context`を実行し、現在の会話履歴から元の入力の`session_id`および`input_id`を取得する。取得した識別子はそのまま使用する。`input_id`は人間による入力を観測した場合にのみ値を持つ。値が`null`の場合は元の入力を取得できていないため、生成された入力などで代替せず、公開範囲の記録を見送る。
2. JSONファイルを作成し、`session_id`、`action: "publish-scope"`、`scope`、`origin`、`input_id`、判断理由を示す`reason`を記載する。既存の作業が存在する場合は`work_id`も含める。`scope`には前述の4つの選択肢の値を指定し、`commitしない`も確定した範囲として渡す。`origin`には、依頼文の指定であれば`instruction`、ユーザーの回答であれば`answer`、既存の恒常的承認であれば`standing-authorization`を指定する。既存の恒常的承認を適用する場合は、`policy_file`にエージェント向け文書の絶対パスを、`input_id`に承認を適用する作業の人間入力の識別子を渡す。
3. `atk run-script termination-evidence -- --decision-file <JSONの絶対パス>`を実行して記録し、成功時に返される`work_id`を後続の工程へ引き継ぐ。

`termination-evidence`の記録処理は、渡された識別子に基づいて元の入力や規範ファイルを読み込み、その由来と実体を確認する。
既存の恒常的承認を適用する場合、規範の全文が記録として保存される。
`quote`は省略可能であり、明示した場合は取得した実体の全文との一致が検証される。
意味の判断および承認の妥当性は、メインエージェント自身が確定させる。
既存の作業に対する回答は同じ`work_id`に関連付け、独立した新しい作業については既存の`start`判断で区切りを設けてから公開範囲を渡す。
作業がまだ作成されていない場合に限り`work_id`を省略でき、元の入力に紐づく新しい作業が作成されて返される。

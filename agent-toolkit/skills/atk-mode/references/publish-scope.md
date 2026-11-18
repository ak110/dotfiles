# 協調モードの公開範囲

協調モードで対象リポジトリの変更を伴う依頼へ着手する前に、公開する範囲を確かめて記録する手順である。
確認するかの判定は`agent-toolkit:atk-mode/playbooks/confirmation.md`「確認要否の判定」に従う。

## 公開範囲の確認

協調モードで対象リポジトリの変更を伴う依頼は、変更へ着手する前に、依頼、過去の回答、恒常的な承認、現行の公開仕様から公開範囲を確かめる。
同じ操作、対象、影響範囲、成立条件を覆う承認があればそのまま適用し、依頼文が公開範囲を指定していればその指定を使う。
不足する承認と未確定の選好だけを、他の確認とまとめて同じ質問ラウンドで確認する。
公開の承認の根拠は、技術的な選択ではなくユーザーの判断に限る。

選択肢は第1案を推奨として次の順で示し、各案の説明に`agent-toolkit:commit/references/publish.md`「公開範囲」の表の「実施する工程」を書く。

1. 既存の判断基準どおり
2. push・CIまで
3. commitまで
4. commitしない

調査だけの依頼、成果物がWIに限られる`add-awi-by-user`の実行中、自律モードでは、この質問を省く。
計画を作る作業では、確認の結果を`## 反映内容と反映先`へ記録する。

### 終了工程の証拠への記録

Claude Codeの協調モードのメインは、公開範囲が指示、回答、既存の承認のどれかで確定した時点で、元の入力と判断を記録する。

1. `atk run-script termination-evidence -- --context`を実行し、元の入力の`session_id`と`input_id`を取得してそのまま使う。
   `input_id`が`null`の場合は元の入力を取得できていないため、生成した入力で代替せず、記録を見送る。
2. JSONファイルへ`session_id`、`action: "publish-scope"`、`scope`、`origin`、`input_id`、判断の理由を示す`reason`を書く。
   `scope`は上の4つの値のどれかで、`commitしない`も確定した範囲として渡す。
   `origin`は、依頼文の指定なら`instruction`、ユーザーの回答なら`answer`、既存の恒常的な承認なら`standing-authorization`とする。
   恒常的な承認では、`policy_file`へエージェント向け文書の絶対パスも渡す。
   既存の作業があれば`work_id`を含め、独立した新しい作業は`start`の判断で区切ってから渡す。
3. `atk run-script termination-evidence -- --decision-file <JSONの絶対パス>`で記録し、返された`work_id`を後続の工程へ渡す。

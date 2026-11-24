# レビュー担当への指示・モデル割り当ての評価セット

レビュー担当への指示とモデルの割り当てを変える変更を、`scripts/review_eval_cases.json`の評価セットから「比べる事例の選び方」で選んだ事例について、変更の前後に比べてから採る手順書である。
指示の例は、実行レビューの役割文書と手順書（`agent-toolkit/skills/atk-mode/roles/exec-review.subagent.md`、`agent-toolkit/skills/atk-mode/playbooks/exec-review.md`）である。
ほかに`agent-basics:review-standards`も対象とする。
見逃しと誤指摘の件数を実例で比べ、改訂が良くなったかを印象ではなく結果で決める。
モデルの費用がかかるため、CIと定期の工程から外して手動で実行する。

## 評価の手順

1. `uv run --frozen python scripts/review_eval.py prepare --case <事例ID> --out <managed-tempの絶対パス>`で、事例ごとの作業ツリーと計画の複製を作る（選んだ事例ごとに`--case`を繰り返す）。
   - 評価は直列を基本とし、同じ入力・版の作業ツリーは担当の起動ごとに複製せず使い回す。
     次の事例・版の環境は前の消費と採点・回収が終わってから用意し、独立した書き込みや同時の比較に別の環境が必要な時だけ増やす。
   - 事例定義の`revision`には、計画、commitの対応記録、WIを取るprivate-notesの完全なOIDを置く。
     WIだけ違う整合版が必要な時は、`wi_revisions`へWIの名前と完全なOIDを書く。
     変更の時点の要求に対応する保存版を原本とし、変更の前後で同じ定義と抽出の原本を使う。
     指定の版の欠落と不正は事例定義を直し、代わりの本文は指定の版に限る。
   - 新形式のAWIを含む事例では、反映の項目ごとの主要な完成状態と出典、リストと段落の補足条件を条件表（`review_contract`）へ写す。
     反映内容の節が無い対象AWIは、H1と冒頭から主要な完成状態を1件作り、人間由来と旧形式の対象外の判定を保つ。
     生成側と起動側は同じ必要条件を検査し、`評価用WI原本`のJSONの一覧から同じ保存本文を取る。
   - 事例定義の`fixtures`に保存版の指定（`revision`と、`plan`か`contract`のパス）がある事例では、評価用の計画と確かめた条件表を保存版から渡す。
   - 出力の`temporary_evidence`は、渡す計画と条件表が参照する一時領域のパスの件数（`referenced`）と、現存しないパス（`missing`）を示す。
     `missing`が空でない事例は、評価の前に、証拠を欠測と明記した評価用の計画を`store`で保存して`fixtures`へ指定する。
   - 出力の`start`をそのまま`agents_server`の`start`へ渡し、比べるモデルは`model_type`で指定する。
     事例ごとの出力先の名前、合成素材の事例の計画の名前とcommit件名は、欠陥の事例と正常対照で同じ形の中立の名前になる。
     事例IDと種類は出力の`case`と`kind`だけにあるため、担当へは`start`だけを渡す。
2. 変更前は稼働中のplugin rootの役割名（`exec-review`）を、変更後は作業ツリーの`agent-toolkit/skills/atk-mode/roles/exec-review.subagent.md`の絶対パスを`subagent_md_path`へ渡す。
   agent-basicsのスキルなど役割文書の外の改訂は、稼働版へ反映した後の実行と、反映の前に保存した結果を比べる。
3. 各担当の終了後、`uv run --frozen python scripts/review_eval.py score --result <事例ID>=<出力のtableのパス>`で、事例別の検出、見逃し、誤指摘、未分類と合計を出す。
   未分類の行は本文を読み、正しい指摘か誤指摘かを手で分類する。
   採点と分類は、担当の返却にある自己申告（読んだ資料、確かめた範囲、解決済みとした判断）に頼らず、担当のセッション記録で実際に読んだ記録と、出力した管理表の行で行う。
4. 採点を保存した後、未分類の無い事例のworktreeをGitで回収する。
   後続の参照がある評価には`--retain-worktree`を付ける。
   worktreeを保持する場合も、依存定義から再生成でき、使用の参照が無い環境、ビルドの成果物、専用のキャッシュは別に回収する。
   Gitの追跡下、symlink、使用中、参照を確かめられない環境は保持し、回収の失敗は次回の`score`で再試行する。
5. 変更後に悪化した事例があれば理由を確かめ、改訂の採否を決める。
   比較結果の保存先を計画の検証結果へ残し、保持した事例は後続の参照が終わってから出力の`cleanup`で作業ツリーを回収する。
   `score`は管理表と同じ場所へ`.score.json`を保存するため、管理表、採点、計画、条件表は回収の後も残る。
   回収に強制削除を使わない。
   未保存の成果などでGitが拒否した時は内容と利用状況を確かめ、保存すべき成果を退避してから同じ`cleanup`をやり直す。

### 事例の追加

障害の事例や振り返りで、レビューの見逃し（レビューの後に判明した欠陥）か誤指摘（レビュー担当の前提の誤りや撤回で採らなかった指摘）が確定したら、`scripts/review_eval_cases.json`へ事例を足す。
メインやレーン担当の技術判断による見送りは、誤指摘に数えない。
入力はprivate-notesの`plans`の計画と`.wi-commits.jsonl`から再構成できるものを選び、期待には、検出すべき問題か出すべきでない指摘を、同じ指摘の行に揃って現れる照合語句（正規表現）で書く。
`prepare`の出力だけでレビューを始められる入力にしてから、次の順で登録する。

1. 条件表を、対象AWIの要求の最終形（後の節で置き換えた方式や条件を反映した形）と読み比べる。
   自動生成の条件表が最終形と違えば、確かめた条件表を保存版にする。
2. 計画が参照する一時領域の証拠は保持期限で消えるため、進捗ログを除き、実機の証拠が欠測であることを明記した評価用の計画を保存版にし、`temporary_evidence`の`referenced`が0件になることを確かめる。
3. 期待に書く問題が、渡す入力で未解決のまま残っていることを確かめる。
   過去のレビューの指摘と判定、補正を記録した進捗行、訂正済みの検証表が入力に残ると、レビュー担当は問題を解決済みと読む。
4. 保存版は`uv run --frozen python scripts/review_eval.py store --case <事例ID> --plan <評価用の計画> --contract <確かめた条件表>`でprivate-notesへ保存し、出力の`fixtures`を事例定義へ書く。
   AWIと計画の本文を含むため、公開リポジトリに置かない。

実行レビュー以外のレビュー担当の事例は、`role`に役割名を、`extra_params`にその役割の起動入力を書いて足す。
値の`{plan}`、`{base}`、`{case_dir}`は、準備の時に計画の複製、開始時のHEAD、事例の出力先へ置き換わる。

## 比べる事例の選び方

改訂が変える判断規則を次の表の行へ対応付け、その行の事例（欠陥の事例と正常対照）を比べる。
各事例が何を検出すれば良いかは、事例定義の`expect`の`description`にある。

| 変える判断規則 | 比べる事例 |
| --- | --- |
| 検証の範囲と証拠（最終差分と直接利用側を覆う検証、探索した利用側の実行と個別の結果） | `small-verification-table-scope`、`small-known-consumer-tests`、`small-verification-evidence-control` |
| 変えた前提を読む側の洗い出しと、読んだ結果の場合分け（不在と読めない場合など） | `small-handoff-record-readers`、`small-handoff-record-readers-control` |
| 入力・既存の状態・操作の順・書き込み操作の組み合わせ、公開ヘルプの入力例、横断テスト | `small-recheck-ledger`、`small-recheck-ledger-control` |
| 方針・規範の配送との整合 | `missed-hook-body-delivery-posttooluse`、`missed-hook-body-delivery-read-table` |
| 指摘の前提の確かめ方（撤回した誤指摘） | `false-redirect-backslash` |
| 投入前レビューの根拠の十分性 | `draft-cache-effect-missing`、`draft-cache-effect-sufficient`、`draft-export-effect-missing`、`draft-export-effect-sufficient` |
| 停止対象の確定と残す機能、設定の作用範囲 | `intent-open-defect`、`intent-open-control`、`intent-fixed-defect`、`intent-fixed-control`、`config-boundaries-defect`、`config-boundaries-control` |
| 設計への問いと確定した仕様の区別 | `design-question-defect`、`design-question-control`、`design-fixed-defect`、`design-fixed-control` |
| 工程の省略判定後に前提が変わる時の判断者と戻り先 | `review-shift-defect`、`review-shift-control` |
| 大量の文章や複数の契約の組み合わせを探索する判断（走査の範囲、読者確認の分担など） | `correct-verification-table-scope`、`missed-known-consumer-tests` |

- 共通のレビュー原則、役割文書の全体の構成、モデルの割り当て（系統、effort）を変える改訂では、全事例を比べる。
- 最後の行の判断は小さい事例では代表できないため、その行に当たる改訂では、他の行で選んだ小さい事例に元事例（全履歴版）を加える。
- 表に当たる行が無い改訂は、各事例の`expect`の`description`から影響を受ける事例を選び、無ければ表へ行と事例を足す。
- 無関係という理由だけで既存の事例と期待を消さない。
- 選んだ事例とその根拠、選ばなかった事例とその根拠を、比較結果と同じ計画の検証結果へ残す。

## 小さい事例

小さい事例は、欠陥の判断に必要な契約・提供側・直接利用側・提出結果だけを持つ合成素材から入力を作り、全履歴版の評価より短い所要時間で比べる。
全履歴版の事例は削除せず、小さい事例の`origin`に元事例（`cases`）か原本の所在（`source`）を書いて対応を保つ。
`prepare`は`origin.cases`が指す事例が評価セットに無い定義を拒否する。

- 素材は`scripts/review_eval_materials/<素材名>/`に置く。
  `base.diff`が変更前の版、事例定義の`material.change`の差分が変更、`material.plan`が渡す計画、同じ場所の`<WI名>.md`が評価用WI原本である。
- 欠陥の事例ごとに、同じ素材で不足を解消した正常対照を用意し、正常対照の`expect.absent`へ欠陥の事例と同じ照合語句を書く。
- 素材には元のAWI・計画の本文を転記せず、判断に必要な関係を一般化して書く。
  無関係なWIや文章は削ってよいが、欠陥の判断に必要な文脈は残す。
  検出語を含むだけの短文や、期待する指摘を計画・WIへ書かない。
- 素材を直す時は`prepare`が作る作業ツリーで編集し、`git -C <作業ツリー> diff --src-prefix=a/ --dst-prefix=b/ HEAD^`の出力で差分を置き換える。
  `scripts/review_eval_test.py`が、各素材の欠陥と正常対照のテストの結果と、照合語句の当たり方を確かめる。

## 投入前レビューの評価

`role: wi-draft-review`の事例では、prepareの`result`が示すパスへ単一原稿の返却の原文をUTF-8で保存し、`score --result <事例ID>=<返却原文の絶対パス>`へ渡す。
exec-reviewは従来どおり`table`のTSVを使う。
投入前レビューは`指摘:`の直下の最上位の箇条を1件とし、字下げの補足を同じ指摘へ含めて照合する。
`なし`は0件で、判定、未解決の指摘数、必要な欄に矛盾があれば採点を失敗させる。
本文の段階は、ユーザー向けの部分の確認と読者別の探索も必要とする。
`確認段階: 本文`の事例では、prepareが原稿をdry-runで検証し、冒頭の抽出と原稿の対応を成立性の証拠に加える。
採点、cleanup、未分類の時の保持は、TSVと同じ経路を使う。

キャッシュの不足の例は、初回の保存版の原要求と本文を保つ。
正常の対照の観測は評価用の仮想の条件であり、実製品のテスト結果を主張しない。
エクスポートの2例も仮想の事例である。
prepareの履歴の再構成用のplanとrevisionと、レビューへ渡すfixtureは区別し、採否はfixtureにある原要求、条件、観測範囲で決める。

## 返す報告に含める項目

選んだ事例とその根拠、事例ごとの変更前後の検出、見逃し、誤指摘、未分類の件数、悪化した事例とその理由、改訂の採否、比較結果の保存先、保持したworktreeとその回収の予定を返す。

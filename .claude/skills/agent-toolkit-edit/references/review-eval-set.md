# レビュー担当への指示・モデル割り当ての評価セット

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

## 評価の手順

対象はレビュー担当への指示とモデルの割り当てを変える変更である。
指示の例は`agent-toolkit/share/exec-review*.md`、`agent-toolkit/share/recurrence-self-check.parent.md`、`agent-basics:review-standards`である。
これらの変更では、`scripts/review_eval_cases.json`の評価セットで変更前後の結果を比べてから採用する。
見逃しと誤指摘の件数を実例で比べ、改訂が良くなったかを印象ではなく結果で判断するためである。
モデルの費用がかかるため、CIや定期工程から外し、手動で実行する。

1. `uv run --frozen python scripts/review_eval.py prepare --out <managed-tempの絶対パス>`で事例ごとの作業ツリーと計画の複製を作る。
   事例定義の`revision`には計画・commit対応記録・WIを取得するprivate-notesの完全OIDを置き、WIだけ異なる整合版を要する場合は`wi_revisions`へWI名と完全OIDを記す。
   変更時点の要求と対応する保存版を原本とし、変更前後で同じ定義と抽出原本を再利用する。
   指定版の欠落や不正は事例定義を直し、代用する本文は指定版に限る。
   新形式AWIを含む事例では、反映項目ごとの主要完成状態と出典、リスト・段落の補足条件を条件表（`review_contract`）へ写す。
   反映内容節がない対象AWIはH1と冒頭から主要完成状態1件を作り、人間由来と旧形式の対象外判定を維持する。
   生成側と起動側は同じ必要条件を検査し、`評価用WI原本`のJSON一覧から同じ保存本文を取得する。
   事例定義の`fixtures`に保存版の指定（`revision`と、`plan`か`contract`のパス）がある事例では、評価用の計画と確かめた条件表を保存版から渡す。
   出力の`temporary_evidence`は、渡す計画と条件表が参照する一時領域のパスの件数（`referenced`）と、現存しないパス（`missing`）を示す。
   `missing`が空でない事例は、評価の前に、証拠を欠測と明記した評価用の計画を`store`で保存して`fixtures`へ指定する。
   出力の`start`をそのままagents_serverの`start`へ渡し、比べるモデルは`model_type`で指定する
2. 変更前は稼働中のplugin rootの役割名（`exec-review`）、変更後は作業ツリーの`agent-toolkit/share/exec-review.subagent.md`の絶対パスを`subagent_md_path`へ渡す。
   agent-basicsのスキルなど役割文書の外の改訂は、稼働版へ反映された後の実行と、反映前に保存した結果を比べる。
   事例の作業ツリーは過去の版のため、そのプロジェクトのスキルの読込表に現行hookが解釈できない行があると、hookはその行を除いた旨を警告する。
   変更前後で同じ保存版を同じhookが読むため比較の条件は変わらず、保存版とhookを変えずに評価を続けてよい。
   警告を受けた評価担当の返却は差し戻さず、比較結果の記録へ、警告で除かれた行による限界を1行記す
3. 各担当の終了後、`uv run --frozen python scripts/review_eval.py score --result <事例ID>=<出力のtableのパス>`で事例別の検出・見逃し・誤指摘・未分類と合計を出す。
   未分類の行は本文を読み、正しい指摘か誤指摘かを手で分類する
4. 変更後に悪化した事例があれば理由を確かめ、改訂の採否を決める。
   比較結果の保存先を計画の検証結果へ残し、出力の`cleanup`で作業ツリーを回収する

障害事例や振り返りで、レビューの見逃し（レビュー後に判明した欠陥）か誤指摘（レビュー担当の前提の誤り・撤回で不採用になった指摘）が確定したら、`scripts/review_eval_cases.json`へ事例を追加する。
入力はprivate-notesの`plans`配下の計画と`.wi-commits.jsonl`から再構成できるものを選び、期待には検出すべき問題か出すべきでない指摘を、同じ指摘行に揃って現れる照合語句（正規表現）で書く。
メインやレーン担当の技術判断による見送りは誤指摘に数えない。
追加する事例は、`prepare`の出力だけでレビューを始められる入力にしてから登録する。

- 条件表を、対象AWIの要求の最終形（後の節で置き換えられた方式や条件を反映した形）と読み比べる。
  自動生成の条件表が最終形と違う場合は、確かめた条件表を保存版にする。
- 計画が参照する一時領域の証拠は保持期限で消える。
  進捗ログを除き、実機の証拠が欠測であることを明記した評価用の計画を保存版にし、`temporary_evidence`の`referenced`が0件になることを確かめる。
- 期待に書く問題が、渡す入力で未解決のまま残っていることを確かめる。
  過去のレビュー指摘と判定、補正を記録した進捗行、訂正済みの検証表が入力に残ると、レビュー担当は問題を解決済みと読む。
- 保存版は`uv run --frozen python scripts/review_eval.py store --case <事例ID> --plan <評価用の計画> --contract <確かめた条件表>`でprivate-notesへ保存し、出力の`fixtures`を事例定義へ書く。
  AWIと計画の本文を含むため、公開リポジトリには置かない。
実行レビュー以外のレビュー担当の事例は、`role`に役割名を、`extra_params`にその役割の起動入力を書いて追加する。
値の`{plan}`・`{base}`・`{case_dir}`は準備時に計画の複製・開始時のHEAD・事例の出力先へ置き換わる。

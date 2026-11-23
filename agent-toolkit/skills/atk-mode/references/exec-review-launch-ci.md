# `CI記録`だけを基準とする実行レビューの起動

実行レビューの起動側が`CI記録`だけの基準で起動する時に、`agent-toolkit:atk-mode/playbooks/exec-review-loop.md`「起動」の手順へ足す前提と例外である。

レビュー基準が`CI記録`だけの起動は、`agent-toolkit:atk-mode/playbooks/process-wi-ci-fix.md`「修正系列」の手順4が起動の条件と渡す値（CI記録の7項目、`修正系列の開始時のHEAD`、`原因commitOID`、`review_contract`）を定める。
起動例: `レビュー基準: CI記録` と3入力に加え、`review_contract: /abs/ci-review-contract.md` を渡す。
単一条項は既存書式の直接入力でも渡せる。

1. CI記録には、原因commitを作った担当がその時に持っていた情報を直接書く。
2. `レーン識別子`の指定と`<レーン識別子>-exec-review`のセッション識別名の規定は、`CI記録`だけによる起動には当てはまらない。
3. 指摘管理表は`agent-toolkit:atk-mode/references/review-record-table.md`「レビュー指摘管理表のパス」の`CI記録`の行に従い、同じ修正系列では同じ表を引き継ぐ。

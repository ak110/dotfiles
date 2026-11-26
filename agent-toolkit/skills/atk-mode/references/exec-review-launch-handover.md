# 引き継ぎ再レビューの起動

前の実行レビュー担当が続けられない時に、起動側が前回の確認範囲を新しい担当へ確実に引き継ぎ、確認の漏れも重複も無く再レビューを終えるための資料である。
実行レビューの起動側が引き継ぎ再レビューで起動する時に、`agent-toolkit:atk-mode/playbooks/exec-review-loop.md`「起動」の手順へ足す前提と例外である。

前の実行レビュー担当が続けられず、新しい担当へ再レビューを引き継ぐ時の起動である（継続できるかの判定と引き継ぎは`agent-toolkit:atk-mode/playbooks/delegation-recovery.md`「継続と新規起動」）。

1. `レビュー種別: 引き継ぎ再レビュー`と、`agent-toolkit:atk-mode/playbooks/exec-review-loop.md`「起動」（手順3の起動方法ごとの資料を含む）で決めた基準ごとの入力を渡す。
2. `前回確認版`には、前の担当の引き継ぎ記録に保存された記録ファイルの絶対パスを渡す。
   記録ファイルは、前回レビューしたHEADと、Git管理外の文章成果物ごとの識別子と前回の本文の複製のパスを含む。
   記録ファイルと比較元の実在を確かめてから起動し、取得できない情報があれば、欠けたHEADか本文の取得元を委譲元へ報告する。
3. `round`には`agent-toolkit:atk-mode/playbooks/exec-review-loop.md`「ラウンド番号の保持先」の値を渡す。
   `review_contract`は`agent-toolkit:atk-mode/references/review-contract.md`「`review_contract`の生成」で作り直す。
4. `引き継ぎ記録先`の`（継続）`の表記は`agent-toolkit:atk-mode/references/delegation-prompt.md`「引き継ぎ記録先」に従い、`rewrite_map`は`extra_params`で渡す。

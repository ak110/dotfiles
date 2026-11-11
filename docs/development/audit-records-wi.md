# 監査記録: WIとレビュー表

[監査記録の索引](audit-records.md)に収録対象と記録の書式を示す。

## agent-toolkit/share/add-wi.parent.md：渡す入力：2026年10月7日

Claude Code 2.1.292のセッションで、要求本文を`extra_params`へ埋め込んだ`agents_server`の`start`が、ホストのJSON解析で拒否された。
リポジトリ外のセッション記録の`__unparsedToolInput.raw`には文字列中の未エスケープの改行と閉じていない末尾があり、ツール結果に`InputValidationError`と`could not be parsed as JSON`が残る。
長文一般や容量上限による拒否を示す観測ではない。

同じセッション記録では`投入する要求`へ要求ファイル`r1.md`の絶対パスと、同書が共通入力`common.md`を参照する旨を渡し、ツール結果にsession識別子と`running`が返った。
これはファイル参照入力による起動の受理を示し、起動結果だけで後続の保存成功まで主張しない。

再検証では、値に生の改行を含み末尾の引用符を閉じていないJSON文字列を標準のJSONパーサーへ渡すと解析エラーになり、正しくエスケープした同じ本文は解析できることを比べる。
不正入力をホストへ再送する必要はない。
現行版でのファイル入力は、公開された`start`のスキーマと`add-wi.parent.md`の入力契約を確認し、要求・共通入力をmanaged-tempへ保存して絶対パスを渡し、起動の終了状態と担当が取得した内容を原文と比べる。

## agent-toolkit/share/pick-wi.subagent.md：調査とレーン分け：2026年8月31日

2026年8月31日から9月2日までの5セッションの観測で、出力トークン量はレーン数へほぼ比例し、レーン数を増やしたセッションで所要時間が短くなる傾向は観測されなかった。

## agent-toolkit/skills/process-wi/references/run-lanes-integration.md「統合とAWI終了」：所有資源の回収：2026年9月3日

2026年9月3日にgit version 2.43.0で、upstreamを設定した専用branchをローカルの`develop`へ統合した直後に`branch -d`が未統合として拒否されることを確認した。
再検証は`git branch -vv`で追跡先を確認して同じ状態の`branch -d`の終了コードを観測する。

## agent-toolkit/share/review-table-operations.md：レビュー指摘管理表の共通操作：2026年10月8日

agent-toolkit 2.199.0の基準commitは2026年10月8日「Merge pull request #147 from ak110/develop」、`3b3932b5`で、review-table実装の差分は無い。
公開CLIで、通常名と`ci-3b3932b.exec-review.tsv`の表をそれぞれ`atk review-table init <パス>`で作成した。
両方とも終了コード0で、`wc -c <パス>`は0バイトを示した。
`atk review-table validate <パス>`も両方が終了コード0で、応答欄を含めた検証結果は0件だった。
ヘッダーと仮の行は追加していない。

再検証ではmanaged-tempへ異なる通常名とCI名のパスを用意し、前記のinit、バイト数確認、validateを順に実行する。
列の順序と旧形式の読み取り互換は`atk review-table --help`で確認する。

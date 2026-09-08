# 統合担当の起動と受領

`agent-toolkit:process-wi`の②のレーンで、呼び出し元が本書を全文読み、統合担当の起動、入力の受け渡し及び返却値の検収へ適用する。
呼び先の作業手順は`${CLAUDE_PLUGIN_ROOT}/share/lane-integration.subagent.md`が定める。本書へ呼び先固有の作業手順を書かない。

## 起動経路

`統合区分`が`マージあり`のレーンでは、当該レーンの最後の実装担当threadへ統合指示を送り、当該threadを統合担当とする。新しい委譲先を起動しない。
`統合区分`が`マージなし`のレーンでは、`agents_server`の`start`へ`model_type="execute"`を渡して統合担当を1件新規起動する。新しい設定キーを追加しない。

## 起動前の前提

`マージあり`では、実行レビューの収束を確定してから統合指示を送る。
`マージなし`では、当該レーンの計画レビューの収束を確定し、人間由来の不採用範囲についてユーザーの確認を完了してから起動する。
いずれの経路でも、AWIファイル名ごとの終端区分を確定してから統合指示を発行する。
`マージなし`の新規起動では、同じ作業ツリーへ書き込む先行主体の終端を確認する。
計画最終化を要する統合では、統合担当が現行plugin rootから自ら解決して実行する`skills/plan-mode/scripts/check_plan_file.py`の実在を、同じ手順でrootを解決して確認する。
この確認は呼び出し元が自ら行う検査であり、解決した絶対パスを統合担当へ渡さない。

## 渡す入力

- `${CLAUDE_PLUGIN_ROOT}/share/lane-integration.subagent.md`の絶対パス
- `統合区分`（`マージあり`又は`マージなし`）
- `マージあり`では、マージ先worktreeの絶対パス、マージ先branch名及び当該レーンの計画ファイル絶対パス一覧。マージ先HEAD完全OIDは渡さない
- `マージなし`では、計画ファイル（メイン・詳細）の絶対パス、プロジェクト規範の絶対パス及び専用worktree作成時のHEAD完全OID
- 計画最終化の要否と、`atk plans commit`へ渡す計画作業root直下のメイン計画ファイル名
- 計画型変換の要否と、`atk wi convert-to-plan`の`--plan-file`へ渡す可搬値及び対象AWIファイル名一覧
- AWIファイル名ごとの終端区分（`adopt`、`reject`又は`終端しない`）と、`adopt`で用いる完全OIDの決定規則
- `終端しない`のうち延期`adopt`対象のAWIファイル名と完全OIDの決定規則。`マージあり`ではffマージ後のベースHEAD完全OIDを用いる規則、`マージなし`では充足根拠として計画に記録された対象commitの完全OIDそのものを渡す。延期`adopt`対象が無い場合は`なし`
- 記録した対象リポジトリの絶対パス
- 作業対象リポジトリへのpush不可という権限
- 完了報告と成果物を日本語で書くこと

`マージあり`で与えた許可はffマージの完了までを範囲とし、rebase競合と再レビューを経た後も同じ許可でffマージまで進む。同じ収束に対して同じレーンへ同じ許可を再送しない。
呼び出し元は、統合担当が`atk agents-notify`で送る`マージ完了`と`merged_head: <ffマージ後のベースHEAD完全OID>`の2行を受領した時点で当該レーンの排他を解除し、次のレーンへマージ許可を与える。
統合担当は当該通知でturnを終端しないため、呼び出し元は`統合完了`の4行を同じレーンから引き続き受領し、「受領と検収」の照合をそのまま適用する。
当該通知の`merged_head`は排他の解除だけに用い、`統合完了`の`merged_head`に対する照合の代わりにしない。
計画最終化、計画型変換及びAWI行の終端はマージ先branchのOIDを変更しないため、排他区間の外で進む。呼び出し元が実行する所有資源の回収も同じ理由で排他区間の外で行う。

## 受領と検収

競合解消の再レビューを要する`対応完了`を受領した場合は、呼び出し元が再レビューを実施し、収束後に同じ許可のまま統合を続行させる。
`統合完了`に続く`merged_head`、`deferred_adopt_commits`、`adopted`及び`rejected`の4行を受領し、次のとおり照合する。いずれかが一致しない場合は同じ統合担当へ差し戻し、成果物と実装差分の再読解をしない。

- `merged_head`が`なし`でない場合は、`git -C <マージ先worktreeの絶対パス> rev-parse <マージ先branch名>`の出力と一致する。`なし`の場合は、当該レーンへ渡した`統合区分`が`マージなし`であることを照合し、Git参照を照合しない
- `deferred_adopt_commits`のAWIファイル名の集合が、起動時に渡した延期`adopt`対象の集合と過不足なく一致する。各完全OIDは`git -C <記録した対象リポジトリの絶対パス> rev-parse --verify <完全OID>^{commit}`の出力と一致する。`マージあり`では各完全OIDが`merged_head`と一致し、`マージなし`ではAWIごとに起動時に渡した対象commitの完全OIDと一致する
- `adopted`が挙げるファイル名の集合が、終端区分で`adopt`と指定した集合と過不足なく一致する。一致を確認した各ファイル名が、`atk wi list --target-repo=<記録した対象リポジトリの絶対パス> --status=adopted --skip-pull --json`の出力へ`filename`として現れる
- `rejected`が挙げるファイル名の集合が、終端区分で`reject`と指定した集合と過不足なく一致する。一致を確認した各ファイル名が、`atk wi list --target-repo=<記録した対象リポジトリの絶対パス> --status=rejected --skip-pull --json`の出力へ`filename`として現れる
件数の一致だけでは、別の項目を終端した報告と終端対象を列挙から省いた報告を区別できないため、集合の一致を先に確認してから各要素の実状態を照合する。
各要素の照合は、当該操作の公開契約が定める終了状態を直接観測して行う。`adopted`は`atk wi adopt`の終了状態が`adopted`への移動であるため、当該状態への存在を観測する。`rejected`も`atk wi reject`の終了状態が`rejected`への移動であるため、当該状態への存在を観測する。`processing`に現れないことは、`inbox`と`hold`への移動、未終端のいずれとも区別しないため、終端の根拠に用いない。
照合はいずれもコマンド1回で判定でき、成果物と実装差分の再読解を伴わない。呼び出し元は成果物、Git状態、検証結果とレビュー表を完了報告の再検収目的で読み直さない。

## 所有資源の回収

4行の照合が全て成立した後に、呼び出し元が当該レーンの所有worktree、所有branchとレーンmanaged-tempを回収する。統合担当へ回収を委譲しない。統合担当の作業ディレクトリは当該worktreeの内側にあり、自身の作業ディレクトリを削除できないためである。
回収対象は、`agent-toolkit/skills/process-wi/references/run-lanes.md`がレーンの入力へ記録すると定める値から解決する。当該記録は、専用managed-tempと専用worktreeの絶対パス、所有主体、作成目的、回収可否、専用branch名、対象リポジトリの絶対パス及び専用worktree作成時のHEAD完全OIDを持つ。当該記録の生成主体は、当該レーンを作成した呼び出し元である。本節の回収も同じ呼び出し元が担う。回収の認可は、呼び出し元が当該worktree、当該branchと当該レーンmanaged-tempの作成主体かつ所有主体であることに由来する。統合担当へ渡す入力から回収対象と回収可の権限を外しても、当該記録は呼び出し元が保持したままである。このため`マージあり`と`マージなし`のいずれの経路でも、同じ手順で回収対象を解決できる。回収対象を保持する記録形式を新たに設けない。
回収の前に、当該レーンの統合担当が終端していることを確認する。回収は`統合区分`に対応する次の手順で行い、対象外worktree、複製元及び管理外領域は削除しない。

- `マージあり`では、branchの削除の直前に、`git -C <マージ先worktreeの絶対パス> symbolic-ref --short HEAD`の出力が起動時に渡したマージ先branch名と一致することを照合する。一致した場合だけ、先に`git -C <記録した対象リポジトリの絶対パス> worktree remove <記録した所有worktreeの絶対パス>`で専用worktreeを削除する。Gitは所有branchをcheckoutしている専用worktreeが存在する間は当該branchを削除しないため、worktreeの削除をbranchの削除より先に行う。続けて`git -C <マージ先worktreeの絶対パス> merge-base --is-ancestor <所有branch名> <マージ先branch名>`を実行する。終了コードが0で所有branchがマージ先branchへ到達済みである場合だけ、`git -C <マージ先worktreeの絶対パス> branch -D <所有branch名>`で削除する。`git branch -d`を先に試さない。`git branch -d`は、対象branchにupstreamが設定されている場合、実行した作業ツリーが指すbranchではなくupstreamを統合判定の基準にする。所有branchにupstreamが設定されている場合、当該upstreamはマージ先branchのリモート追跡refを指し、レーンは作業対象リポジトリをpushしないため当該refはマージ済みのローカルcommitを含まない。この条件では`-d`が常に拒否され、到達確認を経た`-D`だけが成功するため、`-d`の実行と拒否の観測を経路から外す。upstreamを統合判定の基準にする挙動は、2026年9月3日にgit version 2.43.0で、upstreamに`origin/develop`を設定した専用branchをローカルの`develop`へff統合した直後に`branch -d`が未統合として拒否されることを実測した。再検証は`git branch -vv`で対象branchの追跡先を確認したうえで、同じ状態の`branch -d`の終了コードを観測する。
- `マージなし`では、`git -C <記録した所有worktreeの絶対パス> status --porcelain=v1`の出力が空であることを照合する。続いて、`git -C <記録した所有worktreeの絶対パス> symbolic-ref --short HEAD`が記録した専用branch名と一致することを照合する。`git -C <記録した所有worktreeの絶対パス> rev-parse HEAD`が専用worktree作成時のHEAD完全OIDと一致することも照合する。全て一致した場合だけ、`git -C <記録した対象リポジトリの絶対パス> worktree remove <記録した所有worktreeの絶対パス>`でworktreeを削除する。次に`git -C <記録した対象リポジトリの絶対パス> update-ref -d refs/heads/<所有branch名> <専用worktree作成時のHEAD完全OID>`を実行し、期待OIDから更新されていない所有branchだけを削除する。

worktreeとbranchの回収後に、記録したレーンmanaged-tempを`atk managed-temp cleanup --path <レーンmanaged-tempの絶対パス>`で削除する。
回収した所有worktreeと所有branchについて、`git -C <記録した対象リポジトリの絶対パス> worktree list`と`git -C <記録した対象リポジトリの絶対パス> branch --list <所有branch名>`の出力へ対象が現れないことを確認する。
レーンmanaged-tempについては、`test ! -e <レーンmanaged-tempの絶対パス>`の終了コードが0であることを確認する。
照合の不一致、削除の失敗又は確認の不成立を観測した場合は、以降の資源を削除しない。
残存する資源を推測で削除せず、記録値と観測値の差分、残存対象と当該レーンのキュー項目の状態を`agent-toolkit:wi-standards`に従って登録する。
当該レーンを終端して他のレーンを継続する。

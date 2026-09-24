# 終端担当の起動と受領

```text
起動対象: session-termination.subagent.md
```

`agent-toolkit:process-wi`の公開工程で、メインが本書を全文読み、終端担当の起動、入力の受け渡し及び返却値の検収へ適用する。
呼び先の作業手順は`${CLAUDE_PLUGIN_ROOT}/share/session-termination.subagent.md`が定め、本書の記載対象から外す。

## 起動経路

`agents_server`の`start`で終端担当を1件起動する。
`subagent_md_path`には`${CLAUDE_PLUGIN_ROOT}/share/session-termination.subagent.md`を解決した絶対パスを渡す。
`extra_params`には`## 渡す入力`の名前付き入力、`cwd`には対象リポジトリの絶対パスを渡す。新しい設定キーを追加せず、既存のキーの範囲で渡す。
同じセッションでは再度起動しない。

## 起動前の前提

全レーンの終端を確認し、対象リポジトリの主作業ツリーへ書き込む主体が自身だけであることを確定してから起動する。
起動時の`cwd`が対象リポジトリの主作業ツリーであり、現在branchが公開対象のベースbranchであることも確認する。
必須入力の項目名は`bump種別`、`検証・CI方針`、`近接検証結果`、`後続処置AWI`及び`引き継ぎ記録先`とする。これらが起動文にそろっていることと、絶対パスで示す入力が実在することを確認する。

## 渡す入力

- `bump種別`: 各レーンの記録から確定した種別と選定根拠の要約。該当する記録が無い場合は`bump不要`。計画ファイルのパスは渡さない
- そのセッションでpush済みの場合だけ、直前にpushしたcommitの7文字以上の一意な短縮OID
- 固有の終端工程がある場合だけ、`固有の終端工程`として対象と依存順。長時間工程の待機対象と、親が別の実行主体へ渡して並行開始する工程を区別する。後者は情報として示し、終端担当の実行対象から外す
- 延期adoptがある場合だけ、`延期adopt`として対象AWIファイル名、先行する終端工程及び`deferred_adopt_commits`で検収した実装commitのOID又は実装差分が無い項目の空文字列
- `検証・CI方針`: 通常は`通常`。`agent-toolkit:process-wi`の「局所変更の即時公開」が成立する場合だけ`即時対応`
- `近接検証結果`: `即時対応`では成功したコマンドと終了コード。`通常`では`なし`
- `後続処置AWI`: 未完了の独立した後続処置を登録した場合はそのAWIのファイル名。それ以外は`なし`。これは終端担当の報告入力であり、延期`adopt`の対象へ加えない
- `引き継ぎ記録先`: `atk managed-temp create --prefix=handoff`で作成した領域の直下のファイルの絶対パスへ`（新規）`を続けた値。領域の作成と回収は`agent-toolkit/share/managed-temp.md`に従う

対象リポジトリ、ベースbranch名、プロジェクト規範は受信者が`cwd`とGitから解決する。権限は受信者の固定タスク契約を用いる。直前push OID、固有の終端工程及び延期adoptの既定値は`なし`とし、既定値と一致する行は省く。

## 受領と検収

`終端完了`に続く8行を受領し、次のとおり照合する。照合の入力は受領した8行と現在のGit状態とし、成果物と実装差分の再読解をしない。`deferred_adopted`の値はJSON parserで文字列配列として検査し、不正JSON、配列以外又は文字列以外の要素を返却契約の不成立とする。いずれかが一致しない場合は同じ終端担当へ差し戻す。

- `git -C <対象リポジトリの絶対パス> rev-parse --short=7 <ベースbranch名>`と`git -C <対象リポジトリの絶対パス> rev-parse --short=7 <ベースbranchのリモート追跡ref>`の出力が、いずれも`final_branch_head`と一致する
- 通常公開の全体検査を次の条件で照合する。`検証・CI方針`が`通常`の場合、`overall_verification`が`CI判定`又は`ローカル成功`のいずれかだけである。`CI判定`では全体検査とCIの同値性が成立した根拠を`terminal_steps`が挙げる。`ローカル成功`では対象リポジトリのタスクランナーが定める全体検査を1回実行した結果を同じ行が挙げる。統合後にだけ成立する検査をした場合は、検査名、終了コード及び警告の有無も`terminal_steps`が挙げる
- 通常公開のCI結果を次の条件で照合する。`検証・CI方針`が`通常`の場合、`ci_result`が`成功`であり、対象リポジトリのCI照会手段が`ci_verified_head`について同じ結論を返す
- 即時対応方針では、`検証・CI方針`が`即時対応`である
- 即時対応の返却を次の条件で照合する。`検証・CI方針`が`即時対応`の場合、`overall_verification`が起動時に渡した近接検証の成功を挙げ、`ci_result`が`待機省略`とCIのrun URLを挙げ、`terminal_steps`が省略した全体検査と後続処置AWIを挙げる
- `ci_verified_head`と`final_branch_head`が異なり、`final_branch_head`自体のCI成功を前項で検収していない場合は、両方を7文字以上の一意な短縮OIDのまま対象リポジトリのGitコマンドへ渡す。
  `final_branch_head`がマージcommitなら、第1親を`<final_branch_head>^1`として参照し、
  差分commitを`git -C <対象リポジトリの絶対パス> rev-list <ci_verified_head>..<final_branch_head> --not <final_branch_head>^1`で取得する。
  これにより第1親から到達可能なベース側系列を除き、マージcommit本体は集合へ含める。
  `final_branch_head`がマージcommitでない場合は、`git -C <対象リポジトリの絶対パス> rev-list <ci_verified_head>..<final_branch_head>`で取得する。
  いずれもそのOIDの集合が、`terminal_steps`が挙げる生成commitを操作直前に解決したOIDの集合と過不足なく一致することを確認する。
  この代替照合が示すのは差分commitの集合の一致までとし、`final_branch_head`のCI成功の判定は前段の条件で行う
- `base_branch_state`が`公開済み`である
  - `git -C <対象リポジトリの絶対パス> status --porcelain=v2 --branch`を実行する。`# branch.head`の値がベースbranch名と一致し、`#`で始まらない行が0件であり、`# branch.ab`のahead値が`+0`であることを確認する。追跡refが無く`# branch.ab`を取得できない場合は、公開済みと判定しない
  - `git -C <対象リポジトリの絶対パス> status`を実行し、rebase・merge・cherry-pickの進行中を示す表示が無いことを確認する。以上の4項目を現在のGit状態から再取得して全て成立させる。`.git`内部のパス探索と複数のrefを1回へ渡す`git rev-parse --short`を、これら4項目の取得に使わない
  - 現在状態の判定は、この再取得の結果で行う。終端担当が返した`base_branch_state`は再取得の代わりから外す
  - 同じ終端担当への差し戻しでも`公開済み`にならない場合は、`agent-toolkit/skills/process-wi/references/finish-session.md`の「セッション終了」節が定めるUWIの登録へ送る
- `version`が`bump不要`でない場合は、対象リポジトリの版数規範が定める正本ファイルの版数がその値と一致する
- `terminal_steps`が、起動文で終端担当の実行対象とした固有の終端工程を過不足なく挙げる。並行開始する工程の完了は、その担当主体の返却から別に検収する
- `deferred_adopted`のファイル名集合を、起動時にOID又は空文字列を対応付けた延期`adopt`対象の集合と、空集合の場合も含めて比較する。後続処置AWIだけを渡して延期`adopt`が空なら`[]`だけを受理する。同じAWI名が両入力にある場合も、延期`adopt`側の明示入力で照合する
- 延期`adopt`対象がある場合だけ、集合一致の後に全対象のファイル名を渡して`atk wi show <ファイル名>... --target-repo=<対象リポジトリの絶対パス> --skip-pull`を1回実行する。終了コード0と、出力の`### <ファイル名> [<状態>]`の見出し行が全件`adopted`であることを確認する。状態フォルダーの全件取得は、終端項目が累積し続けるため使わない。出力量を抑える場合は`--output-file`へこの工程が所有する管理対象一時領域の絶対パスを渡し、保存したファイルの見出し行を読む

<例>
後続処置AWI=A.md、延期adoptなし: `deferred_adopted: []`。
後続処置AWI=A.md、延期adopt=A.mdとOIDの組: `deferred_adopted: ["A.md"]`。
後続処置AWIなし、延期adopt=B.mdと空文字列の組: `deferred_adopted: ["B.md"]`。
</例>

全ての照合が成立した後は、`agent-toolkit/skills/process-wi/references/finish-session.md`の「セッション終了」節へ戻る。
同節が定めるベースbranchの公開状態の再観測、`agent-toolkit:completion-report`による報告及び`atk agents-exit-session`の実行を実施する。

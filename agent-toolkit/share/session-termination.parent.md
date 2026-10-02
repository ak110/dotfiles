# 終端担当の起動と受領

```text
起動対象: session-termination.subagent.md
```

`agent-toolkit:process-wi`の公開工程で、メインが本書を全文読み、終端担当の起動、入力の受け渡しおよび返却値の検収へ適用する。
呼び先の作業手順は`${CLAUDE_PLUGIN_ROOT}/share/session-termination.subagent.md`が定め、本書の記載対象から外す。

## 起動方法

`agents_server`の`start`で終端担当を1件起動する。
`subagent_md_path`には`${CLAUDE_PLUGIN_ROOT}/share/session-termination.subagent.md`を解決した絶対パスを渡す。
`extra_params`には`## 渡す入力`の名前付き入力、`cwd`には対象リポジトリの絶対パスを渡す。新しい設定キーを追加せず、既存のキーの範囲で渡す。
同じセッションで前回の公開後に新しいcommitを統合した場合だけ、追加差分を入力として再び起動する。

## 起動前の前提

全レーンの終端を確認し、対象リポジトリの主作業ツリーへ書き込む主体が自身だけであることを確定してから起動する。
起動時の`cwd`が対象リポジトリの主作業ツリーであり、現在branchが公開対象のベースbranchであることも確認する。

## 渡す入力

- `bump種別`: レーン数にかかわらず、今回の未公開差分に対して確定した最上位の種別と選定根拠の要約。AWI起草時の単独区分より上位の変更があれば上位区分を渡す。該当する記録が無い場合は`bump不要`。計画ファイルのパスは渡さない
- `直前にpushしたcommit`: そのセッションでpush済みの場合だけ、直前にpushしたcommitの7文字以上の一意な短縮OID。再起動時は前回の公開済み成果と今回の差分を分ける起点とする
- `固有の終端工程`: 固有の終端工程がある場合だけ、対象と依存順。長時間工程の待機対象と、親が別の実行主体へ渡して並行開始する工程を区別する。後者は情報として示し、終端担当の実行対象から外す
- `延期adopt`: 延期adoptがある場合だけ、対象AWIファイル名、先行する終端工程および`deferred_adopt_commits`で検収した実装commitのOIDまたは実装差分が無い項目の空文字列。反映後の新プロセスでしか観測できない条件には、残る完成条件と観測手段も同じ項目へ含める
- `公開対象のadopt済みAWI`: この公開に含むcommitが終端したAWIファイル名の集合。該当項目がある場合だけ渡し、WI状態変更の入力にはしない
- `引き継ぎ記録先`: セッション領域（`agent-toolkit:managed-temp`）の直下のファイルの絶対パスへ`（新規）`を続けた値

対象リポジトリ、ベースbranch名、プロジェクト規範は受信者が`cwd`とGitから解決する。権限は受信者の固定タスク契約を用いる。再起動時の固有終端工程、延期adopt、公開対象のadopt済みAWIは今回の追加差分に属する項目だけを渡す。直前push OID、固有の終端工程、延期adoptおよび公開対象のadopt済みAWIの省略時の値は`なし`とし、省略時の値と一致する行は省く。

## 受領と検収

`終端完了`に続く8行を受領し、次のとおり確認する。確認の入力は受領した8行と現在のGit状態とし、成果物と実装差分の再読解は省く（努力目標。検収の合否は8行とGit状態で判定できる）。`deferred_adopted`の値はJSON parserで文字列配列として検証し、不正JSON、配列以外または文字列以外の要素を返却契約の不成立とする。いずれかが一致しない場合は同じ終端担当へ差し戻す。

- `git -C <対象リポジトリの絶対パス> rev-parse --short=7 <ベースbranch名>`と`git -C <対象リポジトリの絶対パス> rev-parse --short=7 <ベースbranchのリモート追跡ref>`の出力が、いずれも`final_branch_head`と一致する
- 全体検証を確認する。`overall_verification`は`CI判定`または`ローカル成功`のいずれかだけを受理し、`terminal_steps`は`${CLAUDE_PLUGIN_ROOT}/share/session-termination.subagent.md`「出力」の同項目が定める記載条件を満たす
- `ci_result`が`成功`であり、対象リポジトリのCI照会手段が`ci_verified_head`について同じ結論を返す
- `ci_verified_head`と`final_branch_head`が異なり、`final_branch_head`自体のCI成功を前項で検収していない場合は、両方を7文字以上の一意な短縮OIDのまま対象リポジトリのGitコマンドへ渡す。
  `final_branch_head`がマージcommitなら、第1親を`<final_branch_head>^1`として参照し、
  差分commitを`git -C <対象リポジトリの絶対パス> rev-list <ci_verified_head>..<final_branch_head> --not <final_branch_head>^1`で取得する。
  これにより第1親から到達可能なベース側系列を除き、マージcommit本体は集合へ含める。
  `final_branch_head`がマージcommitでない場合は、`git -C <対象リポジトリの絶対パス> rev-list <ci_verified_head>..<final_branch_head>`で取得する。
  いずれもそのOIDの集合が、`terminal_steps`が挙げる生成commitを操作直前に解決したOIDの集合と過不足なく一致することを確認する。
  この代替確認が示すのは差分commitの集合の一致までとし、`final_branch_head`のCI成功の判定は前段の条件で行う
- `base_branch_state`が`公開済み`である
  - `agent-toolkit:commit`の`references/push-and-ci.md`「公開状態の4項目」を現在のGit状態から再取得して判定する。終端担当が返した`base_branch_state`は再取得の代わりから外す
  - 同じ終端担当への差し戻しでも`公開済み`にならない場合は、`agent-toolkit/skills/process-wi/references/finish-session.md`の「セッション終了」節が定めるUWIの登録へ送る
- `version`が`bump不要`でない場合は、対象リポジトリの版数規範が定める定義ファイルの版数がその値と一致する
- `terminal_steps`が、起動文で終端担当の実行対象とした固有の終端工程を過不足なく挙げる。並行開始する工程の完了は、その担当主体の返却から別に検収する
- `deferred_adopted`のファイル名集合が、起動時にOIDまたは空文字列を対応付けた延期`adopt`対象の部分集合であることを確認する。差集合は反映後の新プロセスでしか観測できない完成条件を持つ項目に限り、`terminal_steps`が残る条件と観測手段を挙げることを確かめる
- 延期`adopt`対象がある場合だけ、全対象のファイル名を渡して`atk wi show <ファイル名>... --summary-only --target-repo=<対象リポジトリの絶対パス> --skip-pull`を1回実行する。終了コード0と、出力の`### <ファイル名> [<状態>]`の見出し行が`deferred_adopted`では`adopted`、差集合では`processing`であることを確認する。状態フォルダーの全件取得より名指しの取得を選ぶ（努力目標。終端項目が累積し続け、出力量が増える）

各回の返却をその時点のベースbranch、追跡refおよびCI結果と比べる。全ての確認が完了した後は、`agent-toolkit/skills/process-wi/references/finish-session.md`の「セッション終了」節へ戻る。
検収した公開状態の4項目と、延期対象から`deferred_adopted`を除いた集合を同節へ渡す。間に主作業ツリーもしくは対象refの変更または外部更新が観測された場合は、同節に従って現在状態を取得し直す。
同節で未終端集合の観測と再開記録を処置した後、`agent-toolkit:completion-report`による報告および`atk agents-exit-session`の実行を実施する。

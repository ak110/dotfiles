# 終端担当の起動と受領

```text
起動対象: session-termination.subagent.md
```

`agent-toolkit:process-wi`の公開工程で、メインが本書を全文読み、終端担当の起動、入力の受け渡し及び返却値の検収へ適用する。
呼び先の作業手順は`${CLAUDE_PLUGIN_ROOT}/share/session-termination.subagent.md`が定める。本書へ呼び先固有の作業手順を書かない。

## 起動経路

`agents_server`の`start`で終端担当を1件起動する。
`subagent_md_path`には`${CLAUDE_PLUGIN_ROOT}/share/session-termination.subagent.md`を解決した絶対パスを渡す。
`extra_params`には`## 渡す入力`の名前付き入力、`cwd`には対象リポジトリの絶対パスを渡す。新しい設定キーを追加しない。
同じセッションでは再度起動しない。

## 起動前の前提

全レーンの終端を確認し、対象リポジトリの主作業ツリーへ書き込む主体が自身だけであることを確定してから起動する。
起動時の`cwd`が対象リポジトリの主作業ツリーであり、現在branchが公開対象のベースbranchであることも確認する。
必須入力の項目名は`bump種別`、`検証・CI方針`、`近接検証結果`、`正式対応AWI`及び`引き継ぎ記録先`とする。これらが起動文にそろっていることと、絶対パスで示す入力が実在することを確認する。

## 渡す入力

- `bump種別`: 各レーンが記録したbump種別と選定根拠。該当する記録が無い場合は`bump不要`
- 当該セッションでpush済みの場合だけ、直前にpushしたcommitの7文字以上の一意な短縮OID
- 固有の終端工程がある場合だけ、`固有の終端工程`として対象と依存順
- 延期adoptがある場合だけ、`延期adopt`として対象AWIファイル名、先行する終端工程及び`deferred_adopt_commits`で検収した7文字以上の一意な短縮OID
- `検証・CI方針`: 通常は`通常`。`agent-toolkit:process-wi`の「局所変更の即時公開」が成立する場合だけ`即時対応`
- `近接検証結果`: `即時対応`では成功したコマンドと終了コード。`通常`では`なし`
- `正式対応AWI`: `即時対応`では登録済みAWIのファイル名。`通常`では`なし`
- `引き継ぎ記録先`: `atk managed-temp create --prefix=handoff`で作成した領域の直下のファイルの絶対パス。セッションの管理対象一時領域が通知されている場合は`--session-root <通知された絶対パス>`を付けて作成し、当該子領域は個別に回収せずセッション終了時の回収へ委ねる。独立して作成した領域だけを、当該委譲の全工程の完了後に`atk managed-temp cleanup --path <当該領域の絶対パス>`で回収する

対象リポジトリ、ベースbranch名、プロジェクト規範は受信者が`cwd`とGitから解決する。権限は受信者の固定タスク契約を用いる。直前push OID、固有の終端工程及び延期adoptの既定値は`なし`とし、既定値と一致する行は送らない。

## 受領と検収

`終端完了`に続く8行を受領し、次のとおり照合する。いずれかが一致しない場合は同じ終端担当へ差し戻し、成果物と実装差分の再読解をしない。

- `git -C <対象リポジトリの絶対パス> rev-parse --short=7 <ベースbranch名>`と`git -C <対象リポジトリの絶対パス> rev-parse --short=7 <ベースbranchのリモート追跡ref>`の出力が、いずれも`final_branch_head`と一致する
- 通常公開の全体検査を次の条件で照合する。`検証・CI方針`が`通常`の場合、`overall_verification`が`CI判定`又は`ローカル成功`である。`CI判定`では全体検査とCIの同値性が成立した根拠を`terminal_steps`が挙げ、`ローカル成功`では対象リポジトリのタスクランナーが定める全体検査を1回実行した結果を同じ行が挙げる
- 通常公開のCI結果を次の条件で照合する。`検証・CI方針`が`通常`の場合、`ci_result`が`成功`であり、対象リポジトリのCI照会手段が`ci_verified_head`について同じ結論を返す
- 即時対応方針では、`検証・CI方針`が`即時対応`である
- 即時対応の返却を次の条件で照合する。`検証・CI方針`が`即時対応`の場合、`overall_verification`が起動時に渡した近接検証の成功を挙げ、`ci_result`が`待機省略`とCIのrun URLを挙げ、`terminal_steps`が省略した全体検査と正式対応AWIを挙げる
- `ci_verified_head`と`final_branch_head`が異なり、`final_branch_head`自体のCI成功を前項で検収していない場合は、両方を7文字以上の一意な短縮OIDのまま対象リポジトリのGitコマンドへ渡す。
  `final_branch_head`がマージcommitなら、第1親を`<final_branch_head>^1`として参照し、
  差分commitを`git -C <対象リポジトリの絶対パス> rev-list <ci_verified_head>..<final_branch_head> --not <final_branch_head>^1`で取得する。
  これにより第1親から到達可能なベース側系列を除き、マージcommit本体は集合へ含める。
  `final_branch_head`がマージcommitでない場合は、従来どおり`git -C <対象リポジトリの絶対パス> rev-list <ci_verified_head>..<final_branch_head>`で取得する。
  いずれも当該OIDの集合が、`terminal_steps`が挙げる生成commitを操作直前に解決したOIDの集合と過不足なく一致することを確認する。
  この代替照合を行った場合に`final_branch_head`のCIが成功したものとして扱わない
- `base_branch_state`が`公開済み`である
  - 続けて次の4つの観測項目を現在のGit状態から再取得し、全て成立することを確認する。現在branchがベースbranchであること、作業ツリーがcleanであること、ベースbranchがリモート追跡refよりaheadでないこと、及びrebase・merge・cherry-pickの中断状態が無いことの4つとする
  - 終端担当が返した`base_branch_state`を現在状態の再取得に代用しない
  - 同じ終端担当への差し戻しでも`公開済み`にならない場合は、`agent-toolkit/skills/process-wi/references/finish-session.md`の「セッション終了」節が定めるUWIの登録へ送る
- `version`が`bump不要`でない場合は、対象リポジトリの版数規範が定める正本ファイルの版数が当該値と一致する
- `terminal_steps`が、起動文へ渡した固有の終端工程を過不足なく挙げる
- `deferred_adopted`が挙げるファイル名の集合が、起動時に7文字以上の一意な短縮OIDを対応付けて渡した延期`adopt`対象AWIファイル名の集合と過不足なく一致する。一致を確認した後、全ファイル名を引数として`atk wi show <ファイル名>... --target-repo=<対象リポジトリの絶対パス> --skip-pull`を1回実行する。終了コード0と、出力の`### <ファイル名> [<状態>]`の見出し行の状態が全件`adopted`であることを確認する。状態フォルダーの全件を返す起動形は、当該フォルダーが終端した項目を累積し続けるため用いない。出力量を抑える場合は`--output-file`へ当該工程が所有する管理対象一時領域の絶対パスを渡し、保存したファイルの見出し行を読む

全ての照合が成立した後は、`agent-toolkit/skills/process-wi/references/finish-session.md`の「セッション終了」節へ戻る。
同節が定めるベースbranchの公開状態の再観測、`agent-toolkit:completion-report`による報告及び`atk agents-exit-session`の実行を実施する。

# 終端担当の起動と受領

```text
起動対象: session-termination.subagent.md
```

`agent-toolkit:process-wi`の公開工程で、メインが本書を全文読み、終端担当の起動、入力の受け渡し及び返却値の検収へ適用する。
呼び先の作業手順は`${CLAUDE_PLUGIN_ROOT}/share/session-termination.subagent.md`が定める。本書へ呼び先固有の作業手順を書かない。

## 起動経路

`agents_server`の`start`へ`model_type="execute"`を渡して終端担当を1件起動する。新しい設定キーを追加しない。
同じセッションでは再度起動しない。

## 起動前の前提

全レーンの終端を確認し、対象リポジトリの主作業ツリーへ書き込む主体が自身だけであることを確定してから起動する。
`${CLAUDE_PLUGIN_ROOT}/share/session-termination.subagent.md`の`## 入力`が列挙する必須入力が起動文にそろっていることと、絶対パスで示す入力が実在することを確認する。

## 渡す入力

- `${CLAUDE_PLUGIN_ROOT}/share/session-termination.subagent.md`の絶対パス
- `対象リポジトリ`: 対象リポジトリの絶対パス
- `ベースbranch名`: ベースbranchの名前
- `プロジェクト規範`: プロジェクト規範の絶対パス
- `bump種別`: 各レーンが記録したbump種別と選定根拠。該当する記録が無い場合は`bump不要`
- 直前にpushした完全OID。当該セッションで未pushの場合は`なし`
- `統合後検証`: 統合後検証の検証コマンド。レーン工程で各レーンの計画ファイル（メイン）`## 検証区分`の`統合後検証`行から記録した値を重複なく並べる。全レーンの当該行が`なし`である場合は`なし`
- `固有の終端工程`: 選定工程で記録した固有の終端工程と依存順。無い場合は`なし`
- `延期adopt`: 対象AWIファイル名、先行する終端工程及び`deferred_adopt_commits`で当該AWIに対応付けて検収した完全OID。無い場合は`なし`
- `引き継ぎ記録先`: `atk managed-temp create --prefix=handoff`で作成した領域の直下のファイルの絶対パス。当該委譲の全工程の完了後に`atk managed-temp cleanup --path <当該領域の絶対パス>`で回収する
- `権限`: 作業対象リポジトリへの書込みと`git push`可、固有の終端工程が明示する範囲でのタグ作成とrelease作成可
- 完了報告と成果物を日本語で書くこと

## 受領と検収

`終端完了`に続く8行を受領し、次のとおり照合する。いずれかが一致しない場合は同じ終端担当へ差し戻し、成果物と実装差分の再読解をしない。

- `git -C <対象リポジトリの絶対パス> rev-parse <ベースbranch名>`と`git -C <対象リポジトリの絶対パス> rev-parse <ベースbranchのリモート追跡ref>`の出力が、いずれも`final_branch_head`と一致する
- `post_integration_verification`が、`統合後検証`へ`なし`を渡した場合は`なし`、それ以外は`成功`又は`CI委譲`である。`CI委譲`を受領した場合は、当該検証の結論を`ci_result`の照合で確定し、当該行の値だけを理由に差し戻さない
- `ci_result`が`成功`であり、対象リポジトリのCI照会手段が`ci_verified_head`について同じ結論を返す
- `ci_verified_head`と`final_branch_head`が異なる場合は、両OIDの差分commitを`git -C <対象リポジトリの絶対パス> rev-list <ci_verified_head>..<final_branch_head>`で取得する。当該完全OIDの集合が、`terminal_steps`が挙げる生成commitの完全OIDの集合と過不足なく一致することを確認する。この場合に`final_branch_head`のCIを照会せず、`final_branch_head`のCIが成功したものとして扱わない
- `base_branch_state`が`公開済み`である
  - 続けて`${CLAUDE_PLUGIN_ROOT}/share/session-termination.subagent.md`の「生成物とpush」節を全文読み、同節が定める4つの観測項目を現在のGit状態から再取得して、全て成立することを確認する
  - 終端担当が返した`base_branch_state`を現在状態の再取得に代用しない
  - 同じ終端担当への差し戻しでも`公開済み`にならない場合は、`agent-toolkit/skills/process-wi/references/finish-session.md`の「セッション終了」節が定めるUWIの登録へ送る
- `version`が`bump不要`でない場合は、対象リポジトリの版数規範が定める正本ファイルの版数が当該値と一致する
- `terminal_steps`が、起動文へ渡した固有の終端工程を過不足なく挙げる
- `deferred_adopted`が挙げるファイル名の集合が、起動時に完全OIDを対応付けて渡した延期`adopt`対象AWIファイル名の集合と過不足なく一致する。一致を確認した後、全ファイル名を引数として`atk wi show <ファイル名>... --target-repo=<対象リポジトリの絶対パス> --skip-pull`を1回実行する。終了コード0と、出力の`### <ファイル名> [<状態>]`の見出し行の状態が全件`adopted`であることを確認する。状態フォルダーの全件を返す起動形は、当該フォルダーが終端した項目を累積し続けるため用いない。出力量を抑える場合は`--output-file`へ当該工程が所有する管理対象一時領域の絶対パスを渡し、保存したファイルの見出し行を読む

全ての照合が成立した後は、`agent-toolkit/skills/process-wi/references/finish-session.md`の「セッション終了」節へ戻る。
同節が定めるベースbranchの公開状態の再観測、`agent-toolkit:completion-report`による報告及び`atk agents-exit-session`の実行を実施する。

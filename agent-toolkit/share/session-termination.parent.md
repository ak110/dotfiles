# 終端担当の起動と受領

`agent-toolkit:process-wi`の③で、メインが本書を全文読み、終端担当の起動、入力の受け渡し及び返却値の検収へ適用する。
呼び先の作業手順は`${CLAUDE_PLUGIN_ROOT}/share/session-termination.subagent.md`が定める。本書へ呼び先固有の作業手順を書かない。

## 起動経路

`agents_server`の`start`へ`model_type="execute"`を渡して終端担当を1件起動する。新しい設定キーを追加しない。
振り返りの即時対応と自動コードレビュー監査の是正でcommitした場合は、同じ経路で再度起動する。

## 起動前の前提

全レーンの終端を確認し、対象リポジトリの主作業ツリーへ書き込む主体が自身だけであることを確定してから起動する。
`${CLAUDE_PLUGIN_ROOT}/share/session-termination.subagent.md`の`## 入力`が列挙する必須入力が起動文にそろっていることと、絶対パスで示す入力が実在することを確認する。

## 渡す入力

- `${CLAUDE_PLUGIN_ROOT}/share/session-termination.subagent.md`の絶対パス
- 対象リポジトリの絶対パス、ベースbranch名及びプロジェクト規範の絶対パス
- 各レーンが記録したbump種別と選定根拠。該当する記録が無い場合は`bump不要`
- 直前にpushした完全OID。当該セッションで未pushの場合は`なし`
- ①で記録した固有の終端工程と依存順。無い場合は`なし`
- 延期`adopt`の対象AWIファイル名、先行する終端工程及び`deferred_adopt_commits`で当該AWIに対応付けて検収した完全OID。無い場合は`なし`
- 作業対象リポジトリへの`git push`可、固有の終端工程が明示する範囲でのタグ作成とrelease作成可、CI修正レーンの専用worktree作成と回収可という権限
- 完了報告と成果物を日本語で書くこと

## 受領と検収

`終端完了`に続く6行を受領し、次のとおり照合する。いずれかが一致しない場合は同じ終端担当へ差し戻し、成果物と実装差分の再読解をしない。

- `git -C <対象リポジトリの絶対パス> rev-parse <ベースbranch名>`と`git -C <対象リポジトリの絶対パス> rev-parse <ベースbranchのリモート追跡ref>`の出力が、いずれも`pushed_head`と一致する
- `ci_result`が`成功`であり、対象リポジトリのCI照会手段が`pushed_head`について同じ結論を返す
- `version`が`bump不要`でない場合は、対象リポジトリの版数規範が定める正本ファイルの版数が当該値と一致する
- `terminal_steps`が、起動文へ渡した固有の終端工程を過不足なく挙げる
- `deferred_adopted`が挙げるファイル名の集合が、起動時に完全OIDを対応付けて渡した延期`adopt`対象AWIファイル名の集合と過不足なく一致する。一致を確認した後、各ファイル名が`atk wi list --target-repo=<対象リポジトリの絶対パス> --status=adopted --skip-pull --json`の出力へ`filename`として現れることを確認する
- `released`が挙げる資源について、`test ! -e <当該資源の絶対パス>`が終了コード0を返し、`git -C <対象リポジトリの絶対パス> worktree list`と`git -C <対象リポジトリの絶対パス> branch --list <所有branch名>`の出力へ当該資源が現れない

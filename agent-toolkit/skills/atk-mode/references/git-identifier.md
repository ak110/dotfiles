# Git識別子の扱い

報告、記録、コマンドの操作で使うGit識別子の選び方と、完全なOIDを操作の直前に解決する手順である。
誤った識別子は、意図しない対象への操作や、存在しない対象の待機につながる。
識別子は、コマンドの実行結果として得た値をそのまま使い、記憶や推測で組み立てない（原則3、`agent-basics:writing-standards/references/quality-principles-facts.md`）。

## 読み手ごとの識別子

人間向けの記述（報告、記録、成果物）では、日付、件名、ブランチ名、タグ名、PR・MR番号、実行ログのURL、計画名、WIのファイル名、レーンの識別子など、人間が対象を見分けやすい識別子を使う。
commitの示し方は`agent-basics:writing-standards/references/writing.md`「文章の共通規定」に従う。

エージェントがcommitを機械的に再取得する記述と、工程の間の受け渡しでは、`git rev-parse --short=7 <revision>`が返す7文字以上の一意な短縮OIDを使い、完全なOIDを引き継がない。
`--short`だけを指定すると、最小の長さがGitの設定`core.abbrev`に依存する。
`git rev-parse --short=7`には1回に1件のrevisionだけを渡す（複数を渡すと`fatal: Needed a single revision`で失敗する）。
revisionのpeel式は全体を1つの引数として渡し、PowerShellでは`git rev-parse --verify 'HEAD^{commit}'`のように単引用符で囲む。

## 完全なOIDの解決

外部のコマンドやAPIが40桁か64桁の完全なOIDを求める場合は、操作の直前に同じ実行環境で`$(git rev-parse <revision>)`のように解決して変数へ入れる。
終了コードが0で、値が空でなく、形式が正しいことを確かめてから、その操作にだけ使う。
会話の履歴や古い出力から書き写した値は使わない。
コマンド置換が失敗して空文字列が渡ると操作の意味が変わり、とくに`git push`のrefspecでは、送信元が空になるとリモート参照の削除になる。

## 別のリポジトリの操作

現在の作業ディレクトリと異なるリポジトリを操作するGitコマンドは、`git -C <解決済みの絶対パス>`で対象を各コマンドへ明示し、`cd`の移動を後続のコマンドへ引き継がない。

挙動の確認記録は`docs/development/audit-records-commit-history.md`の次の節にある。

- 「agent-toolkit/skills/atk-mode/references/git-identifier.md：Git識別子の扱い：2026年9月14日」
- 「agent-toolkit/skills/atk-mode/references/git-identifier.md：Git識別子の扱い：2026年9月20日」

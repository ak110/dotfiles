# 監査記録: Git識別子と履歴

[監査記録の索引](audit-records.md)に収録対象と記録の書式を示す。

## agent-toolkit/skills/commit/references/git-identifier.md：Git識別子の扱い：2026年9月14日

2026年9月14日、Git 2.43.0で実際に動かして確かめた。
公式`git-rev-parse`文書は`--short=<length>`を、少なくとも指定長を持つ一意な接頭辞と定める。
`core.abbrev`を設定していない対象HEADでは`git rev-parse --short HEAD`が9文字、`git rev-parse --short=7 HEAD`が7文字を返した。
再検証は`git config --get core.abbrev`の設定有無を記録し、同じHEADに対する`git rev-parse --short HEAD`と`git rev-parse --short=7 HEAD`の文字数を比較し、後者が7文字以上で一意に解決できることを確認する。

## agent-toolkit/skills/commit/references/git-identifier.md：Git識別子の扱い：2026年9月20日

2026年9月20日、Git 2.43.0で`git rev-parse --short=7 HEAD HEAD~1`が標準エラーへ`fatal: Needed a single revision`を書いて終了コード128となることを確認した。
PowerShell 7.6.0では、未引用の`git rev-parse --verify HEAD^{commit}`が同じエラーと終了コード128を返し、単一引用符で囲んだ`git rev-parse --verify 'HEAD^{commit}'`が完全OIDと終了コード0を返した。
再検証は同じrepositoryで1件と2件のrevisionを渡した`--short=7`の終了状態を比較し、PowerShellでpeel式の引用有無によるGitの受理結果を比較する。

## agent-toolkit/skills/commit/references/history-rewrite.md：fixupの実行上の制約：2026年10月8日

Git 2.47.3で、`commit.verbose=true`を設定した隔離リポジトリ2つへ同じ変更をstageし、`#`で始まる行だけを取り除く同じ`GIT_EDITOR`で`--fixup=amend:<sha>`を実行した。
`--no-verbose`付きでは`git log -1 --format=%B`の本文に`diff --git`が無く、外した対照では差分が本文へ入った。
両方とも終了コード0、作業ツリーはcleanだった。

再検証では新規の隔離リポジトリで初期commitを作成し、`git config commit.verbose true`を設定する。
ファイルを変更してstageし、編集バッファの各行を読み、`#`で始まる行だけを除いて保存するスクリプトを`GIT_EDITOR`へ指定する。
`git commit --no-verbose --fixup=amend:<初期commit>`と、別の同一条件のリポジトリで`--no-verbose`を除いた起動を比較し、前記の本文の差を確認する。

## agent-toolkit/skills/commit/references/history-rewrite.md：fixupの実行上の制約：2026年10月18日

Git 2.34.1と2.47.3で、`compare_history_test.py`の隔離履歴に対する20件のテストが成功した。
Git 2.34.1はCIと同じUbuntuの`git_2.34.1-1ubuntu1_amd64.deb`を一時領域へ展開して使用し、SHA-256は`0a0bd5c85eafb688025d68e69f49919591b22abf8b7d608d7e64307714d361ca`だった。
`rebase.updateRefs=true`と別branchを置いた。
文書と同じ`GIT_SEQUENCE_EDITOR=:`、`-c rebase.updateRefs=false rebase -i --autosquash`でfixupを統合した。
最終treeと別branchのOIDが保持され、制御件名が消失した。
Git 2.47.3で局所設定のfalseだけを除いた対照は、別branchのOID保持の試験で失敗した。
履歴検収は空白・path・mode・type・binary・曖昧な対応を区別し、統合後の対応表を公開記録操作へ渡した。

再検証では上記debを展開した`usr/bin`をPATHの先頭、`usr/lib/git-core`を`GIT_EXEC_PATH`へ設定し、`git --version`を確認して同じ20件を実行する。
新しいGitでは通常のPATHで同じテストを実行し、参照更新機能が有効な環境でも局所指定が保護することを確認する。
サブモジュールの作業状態を保持する局所設定は同じautosquashへ併用でき、履歴比較はGit 2.38以降だけの`patch-id --verbatim`を呼び出さない。

## agent-toolkit/skills/commit/references/history-rewrite.md：親だけのGit操作とサブモジュール：2026年10月18日

Git 2.34.1での検証担当の記録によると、`agent-toolkit/agent_toolkit/_atk/submodule_state_test.py`の15件が成功した。
GitはCIと同じUbuntuのdebを一時領域へ展開して使用した。
親の履歴内でgitlinkが異なる隔離fixtureへ再帰trueとfalseを与え、autosquash・通常rebase・keep reset・switch・checkout・restoreを比較した。
再帰trueでは子の作業状態が変わり、呼び出し限りの`-c submodule.recurse=false`では子のHEAD・branch・作業内容を保持した。
比較した値はHEAD・symbolic-ref・porcelain・追跡ファイルの内容・未追跡のレビュー内容である。
autosquashは基点への切替を確実にするため`--force-rebase`を併用した。
局所falseを指定した後も、永続設定の`submodule.recurse=true`を保持した。
同じ実Gitの試験で、private-notes同期のrebaseとkeep reset、laneの準備時のdetached worktreeからbranchへの接続も、子の作業状態を保持した。

再検証ではGit 2.34.1を展開した`usr/bin`をPATHの先頭、`usr/lib/git-core`を`GIT_EXEC_PATH`へ設定し、`git --version`で版数を確認して上記のpytestを実行する。
再帰設定だけを変えた対照の結果と、子の5種類の観測値を確認する。

# 分岐条件の効果の裏付けの運用条件

本書は、`agent-basics:writing-standards/references/testing.md`の「分岐条件の効果の裏付け」を実行するときの運用条件である。

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

## 分岐条件の効果の裏付け

分岐条件の動作確認は一時的なコード置換によって実施し、確認後は確実に元の状態へ復元する。
追跡対象ファイルの復元には`git -c submodule.recurse=false restore --source=HEAD --staged --worktree <パス>`を用いる。
インデックス（staged）と作業ツリー（worktree）の双方を復元し、一時的な変更が以降の検証に残らないようにする。
親リポジトリのみを復元する際は、初期化済みサブモジュールのHEAD、ブランチ、および作業内容を維持する。
なお、置換前に存在していた未コミットの変更は事前に退避しておく。

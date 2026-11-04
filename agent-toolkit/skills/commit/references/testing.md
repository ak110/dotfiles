# テスト方針（全言語共通）

品質基準の正式な提供元は、`agent-basics:writing-standards/references/testing.md`である。呼び出し元の条件と節指定を引き継ぎ、本書の運用条件と正式な資料の必要節・前提・例外を読む。両方に同名の見出しがあれば、その範囲を両方で確認する。範囲の選択と取得済み本文の再利用は`../SKILL.md`「名指し参照の読込方法」に従う。

以下はagent-toolkitが所有する運用条件である。

## 名指し参照の読込方法

名指し参照は`../SKILL.md`「名指し参照の読込方法」に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:writing-standards/references/testing.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/testing.md` |

## 分岐条件の効果の裏付け

分岐条件の動作確認は一時的なコード置換によって実施し、確認後は確実に元の状態へ復元する。追跡対象ファイルの復元には`git -c submodule.recurse=false restore --source=HEAD --staged --worktree <パス>`を用いる。インデックス（staged）と作業ツリー（worktree）の双方を復元し、一時的な変更が以降の検証に残らないようにする。親リポジトリのみを復元する際は、初期化済みサブモジュールのHEAD、ブランチ、および作業内容を維持する。なお、置換前に存在していた未コミットの変更は事前に退避しておく。

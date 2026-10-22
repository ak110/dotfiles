# テスト方針（全言語共通）

品質基準の正式な提供元は`agent-basics:writing-standards`の`references/testing.md`である。呼出元が指定した条件と読込範囲を、同名の正式な品質資料へ引き継ぐ。節指定がある場合は移設先の実見出しへ接続し、指定がない場合は従来の全文読込契約を保つ。

以下はagent-toolkitが所有する運用条件である。呼出元の条件と読込範囲に従い、ここに残る運用節を補う。

## 名指し参照の読込方法

本文の各参照の適用条件を保ち、選んだ資料の指定範囲を取得してから、その基準を使う具体的な判断へ進む。起動時の行の資料は起動本文とともに届く。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:writing-standards/references/testing.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/testing.md` |

## 分岐条件の効果の裏付け

確認は一時的な置換で行い、確認前の状態へ確実に戻す。追跡ファイルの復元には`git -c submodule.recurse=false restore --source=HEAD --staged --worktree <パス>`を使い、indexとworktreeの両方を復元して一時的な変更が検証へ残ることを防ぐ。親だけを復元する呼び出しでは、初期化済みサブモジュールのHEAD・branch・作業内容を保持する。置換前の未コミットの変更は退避しておく。

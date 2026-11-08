# 実装担当とレビュー担当の起動前確認

本書は、実装担当とレビュー担当を起動する前のworktreeと書込所有権の確認を定める。

## 名指し参照の読込方法

名指し参照は`../SKILL.md`「名指し参照の読込方法」に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `mandatory-rules.md#「作業ツリーの保護」` | 判断 | `section:agent-toolkit:*/skills/delegation/references/mandatory-rules.md#「作業ツリーの保護」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `routing.md#「委譲単位の確定」` | 判断 | `section:agent-toolkit:*/skills/delegation/references/routing.md#「委譲単位の確定」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `sending.md#「起動と観測」` | 判断 | `section:agent-toolkit:*/skills/delegation/references/sending.md#「起動と観測」` |

## 実装担当とworktree

- 同一worktreeにおける書き込み作業の分担と交代の手順は`references/mandatory-rules.md`「作業ツリーの保護」が定める。
  レーン内における一時的な内部群の運用は`references/routing.md`「委譲単位の確定」、作業ディレクトリの伝達方法は`references/sending.md`「起動と観測」がそれぞれ定める
- レーン担当者を起動する前に、上流ブランチへの追随が完了しており、staged、unstaged、およびnon-ignored untrackedの変更がすべて空であることを確認する。
  新たなレビュー修正担当者を起動する場合は、レーン担当者の終了を確認した後に、修正内容に関する引き継ぎ記録と現行の未コミット差分（dirty差分）を突き合わせて引き渡す
- 実行レビュー担当者は実装担当者の終了後に起動し、同一の最終HEADに対して1ラウンドにつき1回のみ割り当てる
- 作業用の作業ツリー（複製）で起動する委譲先に対しては、作業に着手する前に`git fetch`を実行し、分岐元のブランチとの差分を双方向で確認して、もし分岐元が進んでいれば先に追随するよう指示する（努力目標）

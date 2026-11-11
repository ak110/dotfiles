# 撤去した仕組みの台帳

dotfilesで導入した後に撤去した仕組みを、何を補う・何を代替する仕組みだったか（働き）で引けるようにまとめる。
新しい仕組み（hook、自動チェック、永続状態、配送経路、規範の条文など）を加える計画の起草時と実行レビューで、名前や名目ではなく働きの語でこの台帳を照合する。
照合と追記の手順は`.claude/skills/edit-dotfiles-norms/SKILL.md`「撤去した仕組みの照合」が定める。

各列の意味は次のとおりである。

- 働き: 仕組みが補っていたこと、または代替していたこと
- 撤去: 撤去または却下の時期と撤去commit。
  時期は記録が示す日付か撤去commitの日付で、commit日と1日前後することがある。
  「事例」「記録」「経緯」を付けた日付は事象や記録の時期で、撤去日を表さない。
  撤去日はcommit欄の撤去commitの日付（`git show -s --format=%cs <commit>`）で確かめる。
  「commit: 未特定」は履歴検索（git log -S等）で撤去差分を確認できなかったことを表す
- 理由: 撤去理由の1文の要約。
  本文は方針の所在を読む
- 方針の所在: 現在守る方針と、その記録の所在（`docs/development/`配下の文書と節）
- 再導入の条件: 所在の本文が条件を示す場合はその要約。
  示されない場合は「撤去理由を解消できることを示した場合」とする

旧hookチェックの2026年9月26日の撤去理由は当時の判断であり、2026年10月2日以降の採否は現行のQCDによる費用比較に従う。
誤削除や移設時の消失を記録した行は、意図的な廃止や再導入の禁止を示さない。

- [hookと配送](removed-mechanisms-hooks.md)
- [工程と担当](removed-mechanisms-workflows.md)
- [WIとキュー](removed-mechanisms-wi.md)
- [CLIとスクリプト](removed-mechanisms-cli.md)
- [記録と証拠](removed-mechanisms-records.md)
- [規範の書き方](removed-mechanisms-writing.md)
- [その他の仕組み](removed-mechanisms-other.md)

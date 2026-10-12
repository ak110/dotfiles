# 委譲先によるprivate-notesへの反映

本書は委譲先が`atk wi`でキュー項目の状態を変更する場合の反映抑止指定の扱いを定める（`agent-toolkit/share/rules-subagent.md`が委ねる）。

- 委譲先は状態を変更する`atk wi`の操作を、反映を抑止する指定を付けずに実行する。連続する操作の中間で`atk wi adopt`・`atk wi reject`の`--skip-push`を使った場合は、最後の操作で指定を外し、未pushのローカルcommitを残さない。残ると他のセッションの同期が分岐する
- `atk plans commit`による計画バンドルの保存は対象外とし、抑止指定を使える条件はその保存を指示する手順が定める

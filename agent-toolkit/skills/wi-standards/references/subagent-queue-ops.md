# 委譲先によるprivate-notesへの反映

本書は`agent-toolkit/share/rules-subagent.md`が委ねる、委譲先が`atk wi`の状態変更操作を行う場合の反映抑止指定の扱いを定める。
AWIまたはUWIの状態遷移を委譲先が扱う場面で読む。

- 委譲先はAWIおよびUWIのキュー項目の状態を変更する`atk wi`の操作を、反映を抑止する指定を付けずに実行する。private-notesへの反映を抑止する指定（`atk wi adopt`と`atk wi reject`の`--skip-push`）を連続する操作の中間で用いた場合は、最後の操作で指定を外し、未pushのローカルcommitを残さずに終える。反映されないローカルcommitは、リモートから取得できず他のセッションの同期を分岐させる
- `atk plans commit`による計画バンドルの保存は前項の対象外とする。保存で反映を抑止する指定を用いてよい条件は、その保存を指示する手順が定める

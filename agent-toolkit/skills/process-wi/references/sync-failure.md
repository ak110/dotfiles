# 直前の同期の失敗

本書は`agent-toolkit:process-wi`の実行順1で、直前の同期結果（`sync-report.json`）の`status`が`failed`か、`post_apply`の`failed_steps`が1件以上ある場合の判定を定める。

- 失敗した段とステップの内容から、このセッションでAWIの処理を完遂できるかを判定する
- 完遂できると判定した場合は、判定した内容と根拠を報告してから次の工程へ進む
- 完遂できないと判定した場合は、AWIの処理へ着手せず、`atk wi process-loop abort`でprocess-loopへ中断を要求し、失敗した段と判定の根拠を報告してセッションを終える

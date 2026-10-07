# 振り返りの準備の失敗

本書は`agent-toolkit:session-review`の工程1で`atk run-script session-review-prepare`が失敗した場合の扱いを定める。

`atk run-script session-review-prepare`が失敗した場合は、診断が一時的な原因を示すなら同じ入力で再実行する。同じ診断での再失敗を確かめた場合と、診断が一時的な原因を示さない場合は`agent-toolkit:wi-standards`に従い、セッションID、失敗事象、原因、解除条件および再現手順を持つ欠陥AWIを登録する。再開工程は「そのセッションをresumeしてsession-reviewを再実行する」とし、transcriptパスは記録の対象から外す。全記録をメインのコンテキストへ読み込んで分析すると、以降の判断へ回す容量が尽きる。
手動起動では分析未完了を報告できる。`agent-toolkit:completion-report`から起動した場合は、分析未完了と欠陥AWIを同スキルへ返し、元作業はそのまま完了させる。

# 振り返りの準備の失敗

本書は`agent-toolkit:session-review`の`SKILL.md`「工程」の振り返りを準備する段落で`atk run-script session-review-prepare`が失敗した場合の扱いを定める。

診断が一時原因を示す場合だけ同じ入力で再実行する。同じ診断で再失敗するか一時原因でなければ、`agent-toolkit:wi-standards`に従い、セッションID・失敗事象・原因・解除条件・再現手順を持つ欠陥AWIを登録する。
再開工程は「そのセッションをresumeしてsession-reviewを再実行する」とする。transcriptパスは記録せず、全記録をメインのコンテキストへ読み込んで分析しない。
手動起動では分析未完了を報告できる。`agent-toolkit:completion-report`から起動した場合は、分析未完了と欠陥AWIを同スキルへ返し、元作業はそのまま完了させる。

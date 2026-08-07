# 処理状況の事前確認

target repoのactive一覧をinbox・processing双方について取得する。同一・関連主題とprocessing項目は
`show`で本文、`target_commit`、`plan_file`、`depends_on`を読む。

関連processing項目にplan fileがあれば対象ファイル一覧も読む。target repoの候補worktreeごとにbranch、
status、`target_commit`以降のcommitと変更ファイルを確認し、計画対象との重なりから対応候補を限定する。
一意に確定できない場合は処理状況を不明と記録し、processing本文を更新せず追随feedbackとして分離する。
process-loopの生存は観測可能な場合の補助情報とし、queueとGit実体を正本とする。

| 観測状態 | 処置 |
| --- | --- |
| 同一・関連項目なし | 新規追加 |
| 重複するinbox | canonicalへ統合し、他項目を同時に不採用終端化 |
| 所有していないprocessing | 新情報無しなら追加無し、追加差分なら依存付き追随 |
| 先行完了だけが必要 | `depends_on`を持つ項目として追加 |
| 実装順序が独立 | 通常追加 |

同一・重複を確定する最小確認の直後に、短いadd経路は統合又は終端、長いplan-and-add経路は予約する。
状態更新より前に追加調査、計画起草、review、本文の清書へ進まない。重複inboxは
`merge-inbox --supersede`で一括処理し、競合でprocessingへ移っていればactive再取得後に判断表を再適用する。

長いproducer工程は`reserve-inbox`で対象inboxを即時に予約付きprocessingへ移す。tokenを保持し、調査、
起草、reviewの各工程境界で期限を確認・更新する。完成時は`merge-inbox`、中止時は
`release-reservation`で予約を必ず解消する。予約中のcanonicalは生tokenを持つ所有経路以外から変更しない。
期限切れ回収は観測した世代・期限によるCAS、不正予約はlock内で不正状態を再検証する`--invalid`に限る。

plan-and-add-feedbackの計画前確認とadd-feedbackの保存直前確認では、後者の最新状態を優先する。

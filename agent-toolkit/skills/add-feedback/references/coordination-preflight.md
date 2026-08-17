# 処理状況の事前確認

対象リポジトリのactive一覧をinbox・processing双方について取得する。同一・関連主題とprocessing項目は
`show`で本文、`target_commit`、`plan_file`、`depends_on`を読む。

関連processing項目に計画ファイルがあれば実装資料の変更説明も読む。対象リポジトリの候補worktreeごとにbranch、
status、`target_commit`以降のcommitと変更ファイルを確認し、計画対象との重なりから対応候補を限定する。
対象worktreeのリモートURLと完全HEADも確認する。計画実装型では完全HEADと計画base commitを照合する。
一意に確定できない場合は処理状況を不明と記録し、processing本文を更新せず追随のフィードバックとして分離する。
`process-loop`の生存は観測可能な場合の補助情報とし、キューとGit実体を正本とする。

| 観測状態 | 処置 |
| --- | --- |
| 同一・関連項目なし | 新規追加 |
| 重複するinbox | `reject --if-inbox`で本文を保持したまま不採用終端化し、完成後に新規追加 |
| 所有していないprocessing | 新情報無しなら追加無し、追加差分なら依存付き追随 |
| 先行完了だけが必要 | `depends_on`を持つ項目として追加 |
| 実装順序が独立 | 通常追加 |

同一・重複を確定する最小確認の直後に、
`atk mq reject <filename> --if-inbox --note=<移管理由>`で重複inboxを終端する。
状態更新より前に追加調査、計画起草、レビュー、本文の清書へ進まない。
計画作成へ移管する場合は、noteに完成後に新規のフィードバックとして投入する旨と計画パスを記録する。
通常のadd経路では、実際の終端理由をnoteに記録する。

状態競合でprocessingへ移っていた場合は当該項目を変更しない。
計画に新情報がある場合だけ、processingのファイル名を`depends_on`へ指定した追随のフィードバックを完成後に追加する。
計画を投入せず終了する場合は、rejectedへ保存した本文を入力として`agent-toolkit:add-feedback`から再投入する。

`agent-toolkit:plan-and-add-feedback`の計画前確認と`agent-toolkit:add-feedback`の保存直前確認では、後者の最新状態を優先する。

# 状態と依存：状態と一覧集合

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

## 状態と一覧集合

| 分類 | 値 | 意味 |
| --- | --- | --- |
| 保存状態 | `inbox` | 次の処理主体による取得を待っている状態。依存が未解決の場合は`blocked`と判定される |
| 保存状態 | `processing` | `agent-toolkit:process-wi`が取得し、現在処理を進めている状態 |
| 保存状態 | `hold` | ユーザーまたはエージェントが編集中のため、自動処理の対象から外れている状態 |
| 保存状態 | `adopted` | 採用した要求の実施が完了した状態 |
| 保存状態 | `rejected` | すべての要求を採用しないことが確定した状態 |
| 導出判定 | `ready` | すべての依存先が終了し、UWIの回答、metadata、および計画が有効で着手可能な状態 |
| 導出判定 | `blocked` | 依存関係の未完了、cooldownの未到来、またはmetadataの不備によって着手できない状態。回答が未保存のUWI自身も`blocked`とみなす |
| 一覧集合 | `active` | `inbox`、`processing`、`hold`の総称。未終了の項目を一覧して確認する範囲を表す |
| 一覧集合 | `processable` | `inbox`と`processing`の総称。実際に着手可能（`ready`）かどうかは個別に判定する |

現在処理すべき項目が重複していないかは、`active`な項目の本文同士を比較して確認する。
過去に同様の要求が存在したかどうかは、`--state=all`を指定して終了済みの項目も含めて取得し判定する。
`agent-toolkit:process-wi`は`processable`に含まれる項目のみを処理の対象とし、候補の選定、優先度の評価、依存関係の判定、および個別指示の読み取りもこの集合から行う。

## 状態遷移

エージェントが実行できる状態遷移を次に示す。
なお、ユーザーはブラウザーUIから任意の状態へ遷移させることができる。

| 遷移 | 操作 | 実行する主体と契機 |
| --- | --- | --- |
| `inbox`→`hold` | `atk wi hold` | 項目の修正が必要と判断した主体が実行する。ユーザーの回答待ちには使わず、「承認型UWIと元項目」の依存で表す |
| `processing`→`hold` | `atk wi hold --state=processing <ファイル名>` | 項目を処理しているセッションが、本文の修正が必要と判断した時点で実行する。エージェント環境では`--state=processing`を付けずに`atk wi hold`を実行すると失敗する |
| `hold`→`inbox` | `atk wi unhold` | 項目を修正した主体が、保存された本文の一致確認を完了した時点で実行する。回答済みUWIを待つ元項目は保留せずに依存で表すため、この遷移の対象にならない |
| `inbox`→`processing` | `atk wi start-processing` | 項目の作業に着手する主体が、開始経路を問わず、着手の時点で項目が`inbox`なら先に実行する。対象は通常のAWI、作業を伴う回答済みUWI（是正を求める回答が保存された事後承認型UWIと、元項目での追加作業を求める回答済み事前承認型UWI）、依存の解消や`unhold`で`inbox`へ戻った元項目である。経路は`agent-toolkit:process-wi`の選定（メインエージェントが処理対象とする項目を確定した直後）、中断した実行の再開、レーン担当が実行中に回答を反映する場合、`agent-toolkit:process-wi`の外部での回答反映を含む。作業を示す`--commit`を付けた`atk wi adopt`が`inbox`の項目を終了すると、CLIはこの遷移の抜けを標準エラーへ警告する |
| `inbox`→`adopted` | `atk wi adopt` | `agent-toolkit:process-wi`のメインエージェントが、回答済みで未終了のUWI（是正要求を含まない事後承認型を含む）を、AWIの処理を開始する前に終了させる際に実行する |
| `processing`→`adopted` | `atk wi adopt` | メインエージェントが回答済みUWIを終了するとき、または作業レーンにおいてマージの完了時やコード変更を伴わない要求充足の確定後にAWIを終了するときに実行する。commitの対応付けでは、実装commitの短縮OID（完全なOIDも指定可能）を`--commit`に渡すと、`## 処理結果`に短縮OIDと件名が記録される。commitが複数ある場合は`--commit <OID>`を繰り返し指定することで、各commitの短縮OIDと件名をペアで残せる。要求単位やcommitに関する補足説明などのメモは`--note`または`--note-file`に記述する。コード差分を伴わずに要求を満たした場合は`--commit`を省略し、充足の根拠を処理結果に記載する |
| `processing`→`rejected` | `atk wi reject` | 計画工程において確定した「全要求の不採用」を、メインエージェントが確認した後にレーンが実行する |
| `hold`→`adopted`または`rejected` | `atk wi adopt`・`atk wi reject` | 保留中の項目について採否が確定し、残作業がない場合に、採否を確定した主体が`unhold`を挟まず直接終了する（一度`unhold`すると、終了する前に`atk wi process-loop`がその項目を取得してしまう可能性があるため） |
| `processing`→`inbox` | `atk wi return-to-inbox` | 上流リポジトリへ投入したAWIの終了を待つ項目を、いったんキューへ戻す際に実行する（[cross-repository-submission.md](cross-repository-submission.md)を参照） |
| `adopted`または`rejected`→`inbox` | `atk wi return-to-inbox --state=<adoptedまたはrejected>` | 誤って採否を確定した項目から過去の処理結果を除去し、再処理のためにキューへ戻す際に実行する |
| `adopted`または`rejected`→`hold` | `atk wi hold --state=<adoptedまたはrejected>` | 誤って採否を確定した項目から過去の処理結果を除去し、確認や修正を行う間保留にする際に実行する |

同じ遷移・メモ・commit・対象リポジトリで終了できる複数の項目は、個別に確認したファイル名を並べ、1回の`atk wi adopt <ファイル名>...`または`atk wi reject <ファイル名>...`で終了する。
メモやcommitが異なる項目は個別に呼び出す。
連続操作の中間で使う`--skip-push`と最後の操作でpushする条件は、`subagent-queue-ops.md`に従う。

実装commitの対応記録の保存や取得、複数commitの指定、および欠落している情報の補完については、`../../commit/references/commit-wi-records.md`「WI実装commitの対応」に従う。
終了後に履歴を書き換える場合は、同様の契約を持つ`plan-rewrite`を用いて対応commitと件名を付け替える。
その際も、採否の結果と処理日時はそのまま保持される。

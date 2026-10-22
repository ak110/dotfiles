# 状態と依存

本書はWIの状態、状態遷移、依存、冷却時間、保留と終端の契約を定める。各遷移を起こす工程の操作手順は、その工程の文書が定める。

## 状態と一覧集合

| 分類 | 値 | 意味 |
| --- | --- | --- |
| 保存状態 | `inbox` | 次の処理主体による取得待ち。依存未解決なら`blocked` |
| 保存状態 | `processing` | `agent-toolkit:process-wi`が取得して処理中 |
| 保存状態 | `hold` | ユーザーまたはエージェントが編集中であり、自動処理の対象外 |
| 保存状態 | `adopted` | 採用内容が完了した終端 |
| 保存状態 | `rejected` | 全要求の不採用を確定した終端 |
| 導出判定 | `ready` | 全依存が終端し、UWI回答、metadataおよび計画が有効 |
| 導出判定 | `blocked` | 依存、cooldownまたはmetadata不備により着手不能。回答が保存されていないUWI自身も`blocked`とする |
| 一覧集合 | `active` | `inbox`、`processing`および`hold`。ユーザーが未終端の項目を確認する範囲 |
| 一覧集合 | `processable` | `inbox`および`processing`。`ready`かは別途判定する |

現在処理すべき項目の重複は`active`の本文で比べ、過去に同じ要求があったかは`--state=all`で終端項目も取得して判定する。
`agent-toolkit:process-wi`は`processable`の項目だけを処理対象とし、候補、優先度、依存判断および固有指示もそこから取る。

## 状態遷移

エージェントが実行できる遷移を次に示す。ユーザーはブラウザーUIから任意の状態へ遷移させられる。

| 遷移 | 操作 | 実行する主体と契機 |
| --- | --- | --- |
| `inbox`→`hold` | `atk wi hold` | 項目の修正が必要と判断した主体、または確認の回答を得られず元項目を進められないと判定した主体 |
| `processing`→`hold` | `atk wi hold --state=processing <ファイル名>` | 項目を処理中のセッションが、確認の回答を得られず元項目を進められないと判定した時点。`--state=processing`を付けないエージェント環境の`atk wi hold`は`processing`の項目で失敗する |
| `hold`→`inbox` | `atk wi unhold` | 修正した主体が保存本文の一致確認を終えた時点。回答済みUWIが指す元項目は「回答済みUWIの取り込み」に従い、`agent-toolkit:process-wi`の外では回答を反映した後に戻す（`agent-toolkit:user-confirmation-and-report`の`references/answer-transitions.md`「回答後の状態遷移」） |
| `inbox`→`processing` | `atk wi start-processing` | `agent-toolkit:process-wi`のメインが処理対象を確定した直後。是正を求める回答が保存された事後承認型UWIと、元項目での作業を求める回答済みの事前承認型UWIも、作業するレーンの対象として遷移させる |
| `inbox`→`adopted` | `atk wi adopt` | `agent-toolkit:process-wi`のメインが、回答済みで未終端のUWI（是正を求めない事後承認型を含む）をAWIの処理開始前に終端する |
| `processing`→`adopted` | `atk wi adopt` | メインが回答済みUWIを終端するか、レーンがマージ完了時か実装変更を伴わない充足の確定後にAWIを終端する。adoptのcommit対応付け: 実装commitの短縮OID（完全OIDも受理）を`--commit`へ渡し、`## 処理結果`に短縮OIDと件名が残る。複数commitでは全OIDと要求単位を`--note`か`--note-file`へ書き、実装差分のない充足では`--commit`を省いて根拠を処理結果へ書く |
| `processing`→`rejected` | `atk wi reject` | レーンが、計画工程で確定した全要求の不採用をメインが確認した後 |
| `hold`→`adopted`または`rejected` | `atk wi adopt`・`atk wi reject` | 保留中の項目の採否が確定し作業が残らない場合に、採否を確定した主体が`unhold`を経ずに終端する（`unhold`すると終端前に`atk wi process-loop`が取得し得る） |
| `processing`→`inbox` | `atk wi return-to-inbox` | 上流リポジトリへ投入したAWIの終端を待つ項目を戻す（[cross-repository-submission.md](cross-repository-submission.md)） |
| `adopted`または`rejected`→`inbox` | `atk wi return-to-inbox --state=<終端状態>` | 誤って終端した項目を、旧処理結果を除いて再処理へ戻す |
| `adopted`または`rejected`→`hold` | `atk wi hold --state=<終端状態>` | 誤って終端した項目を、旧処理結果を除いて確認や修正の間保留する |

実装commitの対応記録の保存、取得、複数commitのnote、欠落の補完は`agent-toolkit:commit`の`SKILL.md`「WI実装commitの対応」に従う。

## 保留と本文修正

エージェントは`inbox`と`hold`の項目を`atk wi rm`で削除でき、ユーザーはブラウザーUIから終端項目も削除できる。本文の編集は`inbox`、`processing`、`hold`のどの状態でも行える。
本文修正が必要と判断した時点、または対象と根拠のある具体的な懸念を受けた時点で、採否の確定や調査を待たず`atk wi hold <ファイル名>`を実行する。保留しない間に別の主体が古い要求を処理し始め得る。保留は調査・認識合わせ・起草から、保存本文の一致確認後の`atk wi unhold`までを覆う。誤投入と確認した項目は`atk wi rm`で削除する。
対象が`processing`なら保留も本文置換もせず、次の処理中の項目の扱いに従う。確かめた後に他主体が`processing`へ移して`atk wi hold`が失敗した場合も同じである。既に`hold`の項目は保留を保ったまま修正と検収を進める。

処理中の項目の扱い: エージェント環境から起動した`atk wi edit`は`processing`の項目の本文置換を拒否する。処理中の要求を書き換えると、そのセッションで処理される内容が変わるためである。エージェントは書き換えたい内容を新しい項目として投入し、処理中の項目へは`--append`による追記だけを行う。ユーザーの操作はこの拒否の対象外である。

## 終端

AWIを終端する主体は、`adopt`のメモの先頭に、`awi-body.md`「節の構成と分担」のH1直下の変更内容を実施した結果として、何をどう変えたかを1文で書く。回答済みUWIでは、回答を取り込んで実際に行った対応を1文で書く。元のAWIでの作業が残る場合は、その未完了の作業と区別する。`reject`のメモの先頭には、不採用の理由を1文で書く。commitやレビューの記録はその後へ置く。メモの語は`SKILL.md`冒頭の語の基準に従う。

`adopt`か`reject`で終端する主体は、実行直前に`--note`か`--note-file`へ渡す処理結果の事実を確かめる。事実は対象のplatform、runtime、呼び出し手段で観測したものとし、別環境の観測は同等性を確かめた範囲に限る。処理結果が参照するcommit、PR、CI run、外部操作は完了を観測し、後続の主体が解決できる識別子を書く。未完了・未観測の処理は、完了形で記録する前に本文を修正する。
`--commit`を渡す採否操作では、revisionを解決でき、originがWIの対象リポジトリと一致するworktreeの絶対パスを`--target-repo`へ渡す（識別だけを要する操作は保存済みの正規化URLを使える）。解決できない場合は終了コード2で状態変更前に停止する。

`rejected`はエージェント由来だけの全要求を技術的根拠で不採用とした場合か、人間由来要求の全てについてユーザーが不採用を明示承認した場合に使う。技術的な失敗、入力不足、外部条件待ち、計画不備はactiveのまま保持し、未回答の不採用確認は採用済み範囲があっても項目を分割せず全体を`hold`で保持する。その他の要因は「着手不能要因の分類」に従う。
技術的に解消できない箇所と続行できない理由が確定した要求単位は、activeのまま残さず未達終端として記録する。

エージェントが自身の誤りで投入した項目は、`atk wi rm`で削除するか`atk wi edit`で正しい要求へ書き直して再利用し、`rejected`は成立する要求の不採用だけに使う。削除はprivate-notesのGit履歴から復旧できるため、確認の対象外とする。
上流リポジトリの改訂を要する要求は[cross-repository-submission.md](cross-repository-submission.md)に従う。

## 依存と冷却時間

`depends_on`は先に終端すべきキュー項目のファイル名を保持し、先行成果が無いと後続WIの安全な実施か完成判定が成立しない場合だけ設定する。依存先ごとに、待つ成果、成立しない工程か完成条件、現行成果物で代替できない理由を後続本文へ書く。同じファイルの変更、書込範囲の重なり、実施順の好み、共通方針の反映は依存の理由にせず、書込競合と実装順はレーン割当で扱う。
依存先は`target_repo`が異なる項目でもよい。同じファイル名が複数の`target_repo`にある場合は依存元と同じ`target_repo`の項目を、無ければそのファイル名の全項目を依存先とする。
依存は`atk wi set-dependencies <filename> --depends-on <filename>`で`inbox`、`processing`、`hold`の項目へ登録する。保存状態を変えないため、保留の区間内で本文と依存を確定できる。`ready`と`blocked`はメタデータの依存だけから導出する。

`cooldown_until`はAWIだけが持つ。初回投入では`atk wi add --cooldown-until <タイムゾーン付きISO 8601日時>`で指定し、複数の`--body-file`へ同じ期限を保存する。空・不正な日時、タイムゾーンなし、UWIやbatchとの併用、通常本文のfrontmatterによる指定は拒否される。既存の`inbox`か`hold`のAWIへは`atk wi edit <filename> --cooldown-until <日時>`で設定し、この編集だけは空文字列で解除できる。UWIへの日時の指定は拒否され、UWIに保存済みの期限は着手可否判定が不正期限として扱うため、同じ編集へ空文字列を渡して解除する。`processing`の項目では設定も解除も拒否される。期限以後は他の着手不能要因がなければ`ready`になる。時間経過で解除されることを現物か公式一次資料で確かめてから設定する。

## 着手不能要因の分類

| 解除される契機 | 当たる要因 | 表し方 |
| --- | --- | --- |
| 時間経過だけ | レート制限のバックオフ、再試行間隔、期日到来待ちおよび外部公開の再確認時点までの待機 | `cooldown_until` |
| 実在する先行キュー項目の終端 | 先行キュー項目の成果待ち。他リポジトリの成果待ちも、待つ対象がキュー項目として実在する場合はここに当たる | その項目のファイル名を`depends_on`へ直接加える |
| ユーザーの回答 | 回答がなければ解除できない要因 | 元項目を保留し、[uwi-format.md](uwi-format.md)の事前承認型の書式に従うUWIを保存する |

UWIにするのは、対処方針の選択か実施可否の判断を要し、問いを1文で書ける要因に限る。外部環境の制約や別環境での実施は発生場所を示すだけであり、UWIの根拠には判断を要することを用いる。認可を得ている実施は`agent-toolkit:process-wi`の外部操作の手順で扱う。
関連作業の完了後に必要になるユーザー対応は、`agent-toolkit:user-confirmation-and-report`「手段の選択」でUWIの登録要否を判定する。必要な場合は関連AWIの完了工程に登録を記録し、そのAWIを処理するエージェントが保存の成功を確かめる。先行作業を待つだけの事項は依存として扱う。
外部公開待ちの`cooldown_until`が解除するのは再確認までの待機だけで、外部対応の完了ではない。外部の解除条件と解除後の作業をAWI本文へ残し、期限到来時に条件を確かめ、未成立か公開日が不定なら次の再確認時点を設定する。

## 承認型UWIと元項目

事後承認型UWIは元の作業を待たせないため`depends_on`へ加えず、元項目は回答前に終端する。
`question_type: post-approval`の事後承認型UWIへ`その対応で問題無い`だけを回答すると、`atk wi answer`と`atk serve`は回答の保存と`adopted`への遷移を同じ操作で行う（保存済みの標準二択`choice`の同じ回答も同様）。一般の`yes-no`、否定回答、追加記述を含む回答は回答済みとして通常の取り込みへ残る。
是正を求める回答は`origin-and-approval.md`「ユーザー専用の記入欄と是正要求」の手順を適用し、そのUWI自体を作業要求として処理する（新しいAWIは起票しない）。

事前承認型UWIは元項目の`depends_on`へ加えず、元項目を`hold`へ移して自動処理から外す（`inbox`なら`atk wi hold <元項目のファイル名>`、`processing`なら`atk wi hold --state=processing <元項目のファイル名>`）。

## 回答済みUWIの取り込み

回答を保存したUWIは、次にそのキューを処理する`agent-toolkit:process-wi`のメインが処理対象へ加える。処理中の`agent-toolkit:process-wi`へユーザーが回答を告げた場合は`${CLAUDE_PLUGIN_ROOT}/skills/process-wi/references/selection-procedure.md`「処理対象WIの追加」で取り込む。メインはUWIと本文が指す保留中の元項目を同じレーンへ割り当て（操作の順序は同書）、そのレーンが回答を反映して両方を終端する。回答が元項目での作業を要さない場合は、メインがUWIを終端して元項目だけを処理対象へ加える。
同じ元項目を本文に持つ未回答のactiveなUWIが残る間は、その元項目も回答済みのUWIも`unhold`・終端・選定しない（1件のUWIは1つの問いを持ち、1つの元項目を複数のUWIが指し得るため）。未回答のUWIの有無は`atk wi grep '<元項目のファイル名>' --type=uwi --answered=no --target-repo=<対象リポジトリの絶対パス> --skip-pull`で判定する。
UWIは元項目のファイル名と回答に要る情報を保持し、再開工程はレーンが元項目の計画の進捗ログから読む。計画が無い場合は、保留時に`atk wi edit <元AWIのファイル名> --append --body-file <絶対パス> --target-repo=<対象リポジトリ>`で元AWI本文へ追記した解除条件と再開工程を読む。

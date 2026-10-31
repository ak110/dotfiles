# セッション振り返り

過去に生じたユーザーからの介入、レビューの反復、手戻り、無駄な作業の再発を防ぐため、対象セッションで発生した問題を特定し、原因と対策を確定させた通常AWIを登録する。
手動起動では、引数に書かれたユーザーが気になっている事項を追加の着眼点として扱う。
その事項だけに対象を限定せず、現在のセッション全体を振り返り、本スキルの準備・分析・投入・結果報告の一連の手順を実施する。
原因と対策の最終判断は振り返りを担当するメインが行い、取得済みの根拠と判断は再利用する。
客観的な事実の収集は、`agent-toolkit:delegation/references/investigation-delegation.md`に沿って独立した担当へ分担できる。
`atk run-script session-review-prepare`コマンドは、セッションに関わった全担当の発話やツール呼び出しの入力を時系列に並べ、各実行結果の成否、データ量、記録位置をまとめたセッション全体の流れ（会話の流れ）を出力する。同時に、あらかじめ除外対象と定められた結果を機械的に取り除いた問題候補ファイル（`candidates.md`）も作成する。候補ファイルだけに目を通すと、定義済みの観点に当てはまらない問題を見落とす恐れがある。そのため、まずは会話の流れの全体から問題を探し、`candidates.md`はその調査で拾いきれなかった事象を補うために活用する。

全記録の抽出やAWIの登録には、元の記録や参照資料、作業キューにアクセスできるファイル操作権限とCLI環境が必要である。これらの機能が利用できない環境では、提示された会話ログの範囲内で問題候補と確認に必要な証拠を整理するにとどめ、セッション全体を対象とした正式な振り返りとは区別して扱う。全記録の検収および正式なAWI登録は、必要な機能を利用できるホスト環境において、最上位のメインエージェントが実施する。問題候補の記述内容については、[文章の品質基準](quality-standards.md)に適合しているかを確認する。

## 名指し参照の読込方法

本文で名指しした資料と読込表の資料は、条件が成立したらその判断や操作の前に自分で全文読み、指定箇所に注目してから、その基準で判断する。読込表の列の使い方は`agent-basics:writing-standards`の`references/quality-principles.md`「読込表の読み方」に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文の参照基準を適用する前 | `../../user-confirmation-and-report/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/user-confirmation-and-report/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `quality-standards.md` | 判断 | `read:agent-toolkit:*/skills/review-session/references/quality-standards.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../wi-standards/SKILL.md#「投入と取得」` | 判断 | `section:agent-toolkit:*/skills/wi-standards/SKILL.md#「投入と取得」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../report-completion/references/report-formats.md` | 判断 | `read:agent-toolkit:*/skills/report-completion/references/report-formats.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:bugfix/SKILL.md` | 判断 | `read:agent-basics:*/skills/bugfix/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:bugfix/references/root-cause-analysis.md#「原因分析の品質確認」` | 判断 | `section:agent-basics:*/skills/bugfix/references/root-cause-analysis.md#「原因分析の品質確認」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:bugfix/references/root-cause-remediation.md#「再発防止策の必須性」` | 判断 | `section:agent-basics:*/skills/bugfix/references/root-cause-remediation.md#「再発防止策の必須性」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../report-completion/references/report-formats.md#「振り返り結果報告のフォーマット」` | 判断 | `section:agent-toolkit:*/skills/report-completion/references/report-formats.md#「振り返り結果報告のフォーマット」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../user-confirmation-and-report/references/communication-methods.md#「手段の選択」` | 判断 | `section:agent-toolkit:*/skills/user-confirmation-and-report/references/communication-methods.md#「手段の選択」` |
| 読込表と本文の名指し参照を読む前 | `agent-basics:writing-standards/references/quality-principles.md`の「読込表の読み方」 | 判断 | `section:agent-basics:*/skills/writing-standards/references/quality-principles.md#「読込表の読み方」` |

## 読込表

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 | 資料選択子 |
| --- | --- | --- | --- | --- |
| 工程1のprepareが失敗した時 | `analysis-failure.md` | 判断 | `read:agent-toolkit:*/skills/review-session/references/analysis-failure.md` | |
| 準備済みの対象を分析する前（工程2〜5） | `session-analysis.md` | 判断 | `read:agent-toolkit:*/skills/review-session/references/session-analysis.md` | |
| 効果候補を判定し保存する前 | `analysis.md`の「原因と対策」 | 判断 | `section:agent-toolkit:*/skills/review-session/references/analysis.md#「原因と対策」` | |
| 事実関係の調査を分担させる前 | `agent-toolkit:delegation/references/investigation-delegation.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/investigation-delegation.md` | |
| コスト削減案と所要時間超過への処置を確定する前 | `duration-analysis.md` | 判断 | `read:agent-toolkit:*/skills/review-session/references/duration-analysis.md` | |

## 起動するエージェント

手動の本スキルは、現在のセッションを統括するメインが起動する。
通常委譲先は独自に重複した振り返りを起動せず、観測した事象と調査結果を委譲元へ返す。
各セッションの終了時には本スキルを呼ばず、SessionStartで登録された待ちをbatchの担当が非活動時にまとめて処理する。
中断・利用上限・未再開の記録も、最終報告の有無にかかわらず待ち集合と実際の処理範囲で扱う。
作業完了報告・公開・ホスト停止は元作業のメインが担い、非同期振り返りとは独立して進める。
再発防止策となるAWIの本文起草とキューへの登録は、`agent-toolkit:wi-standards`「投入と取得」に従い、WI投入担当へ委譲して行う。
振り返りのメインは原因分析と類似見直しを実施し、具体策を選定して結果をWI投入担当へ渡す。投入担当と実行担当は確定済みの調査を繰り返さず、対象の変化や新たな証拠がある場合に必要な範囲を再確認する。

## 工程

### 工程1

prepareは会話・問題候補・統計に加え、効果候補の`effect_candidates_path`と件数、効果専用の未確認の`effect_unconfirmed_path`と件数を返す。
効果候補は元セッション開始前14日以内（両端を含む）に採用された未確認AWIのうち、読込・起動・hookの読込指示・常時rulesへ対応する変更ファイルである。
`--after`による差分の開始位置は、この14日間の基準に使わない。
共通分析でファイル別に判定し、保存と報告は`analysis.md`「原因と対策」に従う。

次のいずれかの形式で`atk run-script session-review-prepare`を1回実行する。出力先となる作業領域は、実行ごとに対象セッションのmanaged-temp内に自動で確保される。引数として、Claude Codeでは現在のtranscriptの絶対パス（`rules-main.claude-code.md`「自身の会話の記録」に記載された取得元の値）を、Codexでは`CODEX_THREAD_ID`を、OpenCodeでは対象セッションの`ses_`から始まるIDを指定する。`--target-repo`は、対象リポジトリを確実に特定できた場合のみ指定する。

```text
atk run-script session-review-prepare -- --transcript <transcriptの絶対パス> --target-repo <対象リポジトリの絶対パス>
atk run-script session-review-prepare -- --codex-thread-id <thread ID> --target-repo <対象リポジトリの絶対パス>
atk run-script session-review-prepare -- --opencode-session-id <session ID> --target-repo <対象リポジトリの絶対パス>
```

OpenCodeを対象とする場合、スクリプトは環境変数`XDG_DATA_HOME`（未設定時は`~/.local/share`）配下の`opencode/opencode.db`を読み取り専用で参照する。データベースの保存先を明示する場合は、`--opencode-db <SQLiteの絶対パス>`を追加する。
対応する記録形式はOpenCode 1.19.3の`session`・`message`・`part`構造であり、`parent_id`をたどって子孫メッセージを収集する。
記録位置を示す行番号（line）は、セッション内のメッセージやパートの並び順に基づく論理位置を表す。詳細照会すると、テーブル名と元の行IDも確認できる。
元のデータベースが更新されると論理位置がずれる可能性があるため、生成済みの証拠データは同じbundle内から読み込む。
データベースやセッションが存在しない場合、スキーマが未対応の場合、あるいはデータ取得に失敗した場合は終了コード2となる。その際は、対象の記録を保持しているホスト上で同じIDを指定して`atk run-script session-review-prepare`を再実行する。
取得できなかった範囲は未確認領域として管理し、セッション全体の取得、分析、および報告がすべて完了した段階で振り返りの完了を判定する。

成功した1行JSONを自身のmanaged-tempへ保存し、`host`、`session_id`、`generation`、`scope_path`を工程6の範囲確定まで保持する。`work_dir`から確保された出力先ディレクトリを取得する。生成された成果物は処理後もそのまま保持され、セッション用managed-tempの規定に従って7日間保存される。なお、呼び出し側が`--work-dir`で既存の絶対パスを明示的に指定した場合は、そのディレクトリの保持と削除を呼び出し側自身で管理する。

指定した作業ディレクトリ内には、会話の流れ（`conversation.md`）、比較材料（`comparison-materials.md`）、問題候補（`candidates.md`）、セッション統計（`stats.md`）の4つの文書が出力される。

- `conversation.md`: 実際のユーザー発話ごとの時系列に沿って、メインエージェントの動きや各委譲先の目的、返却内容、操作量を記録位置とともに原則100,000バイト以下でまとめた文書である。本文には実際の作業概要を記載し、全体の詳細テキストは記録位置を`--detail`オプションに渡すことで取得できる。
- `comparison-materials.md`: 委譲時の入力内容や規範の読み込み状況を記録ごとに整理した参照資料である。対象、発生時点、記録位置を手がかりにして、詳細照会で全文を取得すべき位置を特定するために用いる。全体の時系列は会話の流れから把握する。
- `candidates.md`: 70,000バイト以下にまとめられた問題候補の索引であり、問題の種別、発生件数、評価の必須性、代表的な発生位置、概要を示す。全候補のID、詳細との対応関係、過去の類似事例は`bundle/review-index.jsonl`に整理されている。質問内容、全選択肢、実際の回答、全発生位置は`bundle/candidates.jsonl`に、個別の証拠原文は`bundle/candidate-evidence.jsonl`にそれぞれ整理されている。一覧に表示しきれなかった候補も含めて全件を突き合わせ、必須とされた候補は漏れなくすべて評価する。
- `stats.md`: 30,000バイト以下にまとめられたセッションの統計情報である。全担当者を母集団とした処理総量や集計の定義、担当者別のトークン消費上位、処理の律速区間、コンテキスト圧縮や未計測の状況、ドキュメント読込・Skill起動・反復実行・出力データ量の上位項目を提示する。担当別や対象別の詳細データは`bundle/stats.jsonl`および`bundle/resource-usage.jsonl`に保持されているため、一覧に載っていない担当者や律速以外の担当者についても照会できる。常に目を通す索引情報と保存用の詳細データを明確に分離し、判断に必要な詳細のみを追加で取得する。
- `stats.md`の大出力点検: 記録された返却本文が8,192バイト以上のものについて、単独で明示的に取得した本文を除き、データ量の大きい順に最大10件の入力内容と出力位置を示す。不要な項目の取得、過度な反復、画面表示とファイル保存の混在がないかを見直す。ただし、出力の大きさや反復の回数だけで直ちに問題と断定することは避ける。
- `candidates.md`および`stats.md`の未確認範囲、ならびにJSONの`unconfirmed_scope`: 原因が未解決の記録や、識別子が取得できなかったツール呼び出しを区別して提示する。これらは問題候補の件数とは別に確認すべき対象である。
- 記録行の破損への対応: JSONとして不正な記録行であっても、直後に同じ`ordinal`と`type`を持つ完全な行が存在する書き込み途中のデータであれば、完全な行へ置き換えて読み込む。置き換えが行われた行は、`candidates.md`や`stats.md`の「未確認範囲」セクション内の「回復した記録行」に記載され、JSONの`recovered_record_lines`や`bundle/recovered.jsonl`からも全件を確認できる。それ以外の破損行は自動回復しない。メインエージェントの記録に破損がある場合は行番号を明示して準備処理を停止し、委譲先の記録に破損がある場合は理由を添えて`unconfirmed_scope`に記録する。

標準出力に出力される1行のJSONには、各生成文書のパス（`conversation_path`、`comparison_materials_path`、`candidates_path`、`stats_path`）および参照文書のパス`reference_document`（存在しない場合はnull）が含まれる。
このほか、`prepared_at`、`utterance_counts`、`candidate_total`、`candidate_counts`、`excluded_counts`、`failure_ledger_skipped`が格納される。
さらに、`mandatory_candidates`、`similar_records`、`elapsed_seconds`、`compaction_count`と、実際に抽出した記録の範囲を示す`scope_path`も含まれる。これらの候補を振り返り結果報告のどの項目に記載するかは、`agent-toolkit:report-completion`の`references/report-formats.md`に従う。
`conversation_utf8_bytes`、`candidates_utf8_bytes`、`stats_utf8_bytes`は生成された各文書のUTF-8バイト数を示し、`conversation_limit_bytes`は会話の流れの原則上限である100,000バイトを示す。候補文書と統計文書のサイズは、合計で100,000バイト以下に収める。
`improvement_lines`には、セッション記録の全文から抽出された実際の改善提案行が格納される。候補文書には先頭10件の概要と総件数が表示され、全行の内容は`bundle/timeline.jsonl`の`reported_improvements`から確認できる。また、未解決の記録全件は`bundle/unconfirmed.jsonl`に保存される。
2〜5. `session-analysis.md`の工程2〜5を実施する。

### 工程6

AWIの保存と検収、全候補と未確認範囲の判定を終え、今回分析した範囲と残る範囲を確定する。
その場で調査済みの改善点は根拠と判断を再利用し、未調査・変更・不一致の部分だけを補う。標識の転記や同じ行の再掲には既存の分析結果を対応付ける。
準備結果の`scope_path`と、準備後の追記や再開による増分を区別する。今回の要求に必要な追加対応は既存証拠とピンポイントの照会で進め、未処理の増分は次の振り返り待ちとして保持する。
最終報告を送達する前（通常のAWIの保存・検収を完了した後。登録件数が0件の場合や、最終報告の作成作業中に初めて制限時間を超過した場合も含む）に、本スキルの起動時刻からの経過時間を確認する。所要時間が300秒を超過しており、まだ対応が行われていない場合は、手元にある統計情報や証拠データ、準備完了後の追加照会結果を用いて、工程3と工程4に沿って所要時間の超過原因を分析する。判断に必要な情報がどうしても不足する場合に限り、完了したWI投入担当の記録も含めた最新のセッションログを用いて統計データを再準備する。同じ超過事象について原因の特定、必要な全処置、報告内容がすでに確定している場合は、単に秒数を再計算するためだけのログ再収集や分析の反復を省略する。時間超過の判定には振り返り処理そのものに要した経過時間を用い、セッション全体の経過時間（`elapsed_seconds`）と混同しないよう注意する。時間超過に伴って追加のAWI登録が必要となった場合は、工程5と同様に追加分の予告を出力した上で、既存のWI投入担当による起草、メインエージェントによる検収、ファイル保存を実施する。既存のAWIで対処済みであるという結論や、再発防止策を策定しないという判断に至った場合も、その妥当な根拠とともに今回の最終報告に記載する。今回のセッションにおける処理の律速要因に対する対応は、AWI起草基準の見直しといった別の課題に対する事前の回答とは明確に区別して確定させる（詳細は`duration-analysis.md`「所要時間の分析と改善」を参照）。
手動起動では分析・必要なAWI保存・結果本文の確定を終え、準備時の1行JSONを保存した絶対パスを`atk run-script session-review-state -- complete --prepared <準備結果JSONの絶対パス>`へ渡す。成功と残る待ち件数を確認し、結果本文を送達する前に必要なツール処理を終える。この操作の確定対象は準備済み範囲の分析に限る。本文の送達は実際の送達結果で確かめ、分析に失敗した範囲は未処理として保持する。
結果本文は`agent-toolkit:report-completion`の`references/report-formats.md`に従う。AWIを1件以上投入した場合は「AWI投入結果報告のフォーマット」、それ以外は「振り返り結果報告のフォーマット」を使う。送達は`agent-toolkit:user-confirmation-and-report`に従う。本スキルは結果報告の送達で終え、通常作業の完了処理と同期振り返りの再起動は対象外とする。

振り返り工程全体の所要時間目標は300秒とする。この所要時間の計測は、本スキルの起動時点から最終報告（AWIを1件以上登録した場合は`## AWI投入結果報告`、0件の場合は`## 振り返り結果報告`）を出力するまでの時間を対象とし、事前の予告通知を出力した時点は終了点とみなさない。この目標時間は振り返り処理の完了条件そのものではなく、時間を超過した場合は工程6に従って原因を分析し、必要な処置を行い報告する。

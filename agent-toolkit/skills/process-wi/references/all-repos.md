# 登録全体のAWI処理

本スキルを起動した統括担当は、最上位のメインとして登録した全対象をprocess-wiの実行として完遂する。
集合JSONを受領した統括担当は、そこにある候補・所有・準備先を使用する。
開始時候補を再取得せず、対象別の`candidate_file`を選定の原本とする。
巡回ごとに統括会話を1回起動し、次の巡回では新しい会話が残件を選定する。
明示resumeでは保存済みの会話と未完了成果を引き継ぐ。

対象別の選定・監査・公開には[共通の受渡し規範](all-repos-orchestration.md)を適用する。

## 名指し参照の読込方法

本文で名指しした資料と読込表の資料は、条件が成立したら判断や操作の前に指定節と、その成立に必要な前提・例外を自分で読む。全体が判断に必要な場合は全文を読む。読込表の列の使い方は`agent-basics:writing-standards`の`references/quality-principles.md`「読込表の読み方」に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 共通の受渡し規範を選択して取得する時 | `all-repos-orchestration.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/references/all-repos-orchestration.md` |
| リポジトリ担当の処理規範を選択して取得する時 | `../SKILL.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/SKILL.md` |
| 統括がlane_limit 2以上の対象を直接処理する時 | `lanes.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/references/lanes.md` |
| 統括がlane_limit 1以下の対象を直接処理する時 | `single-lane.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/references/single-lane.md` |
| リポジトリ担当の引き継ぎ記録先を指定する時 | `../../delegation/references/base-contract.md#「引き継ぎ記録先」` | 判断 | `section:agent-toolkit:*/skills/delegation/references/base-contract.md#「引き継ぎ記録先」` |
| 読込表と本文の名指し参照を読む前 | `agent-basics:writing-standards/references/quality-principles.md`の「読込表の読み方」 | 判断 | `section:agent-basics:*/skills/writing-standards/references/quality-principles.md#「読込表の読み方」` |

## 開始と振り分け

集合JSONを伴わない手動起動では、`atk wi process-loop --once`を実行する。
この公開操作が対象別の所有・候補ファイル・worktreeを準備し、登録全体を1回処理する統括会話を起動する。
起動元はCLIの終了まで観測し、返却結果をユーザーへ伝える。
対象の作業・完了報告・報告用UWIは起動された統括に任せる。
対象0件では待機せず終了し、未完了・所有待ち・準備失敗は非0で終了する。
以降は集合JSONを受領した統括担当へ適用する。
集合JSONの`repositories`を対象集合とする。
各対象の準備済み`worktree`を処理の起点とする。
`ownership_record`と`ownership_token`は常駐が取得済みの所有であり、配下の担当へそのまま渡す。
`lane_session_id`はリポジトリ単位で保持し、レーン操作の`--session-id`へ明示する。

対象worktreeの作業に入る前に、その対象の`AGENTS.md`、`CLAUDE.md`、作業に関わるプロジェクトスキルの実在する`SKILL.md`を絶対パスで読んで適用する。
ホームなどのプロジェクト外cwdでは、対象のプロジェクトスキルを明示的に読み込み、通常のプロジェクトhookが担う確認を実施する。
対象の規範と既存チェックを適用してhookの目的を満たす。
担当の振分け前に、`agent-toolkit/rules/01-agent.md`「行動と手順の目的」に従い、今回の対象集合で委譲が得る成果と直接処理との差を点検する。
WorktreeCreate/WorktreeRemoveは対象設定を読む既存atk操作で実行し、常駐が準備したworktreeを利用する。

初回の担当起動前に、取得済みの候補本文・件数・readiness・previousの残工程から、今回進められる処理負荷が最大と見込まれる1対象を選び、統括自身が直接担当する。
変更規模、検証・公開、再開時の残作業を比べ、差を判断できなければ候補数、それも同じなら受領集合の順で選ぶ。
選定理由は既存の引き継ぎ記録へ短く残し、詳細な実装調査や専用の負荷スコアは作らない。
対象が1件なら上限によらず直接処理し、全対象の`lane_limit`が1以下なら従来の集約を保って統括が全件を処理する。
上限2以上の対象がある複数対象では、直接担当を除く1以下の対象群を全件まとめて1つのリポジトリ担当へ渡し、残る2以上の対象は対象ごとに並行委譲する。
軽い残対象がある場合だけまとめ担当を起動し、1件の場合もその担当へ渡す。
`lane_limit`は対象内の並列度と軽い対象の区分に限定して使い、負荷最大の対象は上限によらず直接担当に選べる。
統括の直接担当、軽い群のまとめ担当、重い対象の個別担当を並行して進める。
直接処理では`lane_limit`が2以上なら[通常のレーン処理](lanes.md)、1以下なら[単一レーン処理](single-lane.md)へ接続する。
両経路とも受領済み候補・readiness・対象worktree・対象別`lane_session_id`を使い、起動したレーン担当とwriterを対象別の`writer_sessions`へ記録する。
対象worktreeごとに計画・実行レビュー・公開を分ける。
対象別の開始時CI点検、監査、UWI、公開とCI検収を実施する。

## リポジトリ担当の起動と受領

```text
起動対象: repository-process.subagent.md
```

### 起動

リポジトリ担当の起動は定型作業であり、agents_serverの`start`をtaskで呼ぶ。
`model_type`は指定せず、役割文書が定める`orchestrate`を使う。

| 引数 | 値 |
| --- | --- |
| `cwd` | 単一対象の準備済み`worktree`。まとめ担当ではグループ内の準備済みworktreeの1つ |
| `subagent_md_path` | `repository-process` |
| `extra_params` | 次の名前付き入力だけ |

- 単一対象では`target_repo`、`worktree`、`candidate_file`、`readiness`、`lane_limit`、`ownership_record`、`ownership_token`、`lane_session_id`に集合JSONの対象要素の同名の値を渡す
- まとめ担当では、割り当てる対象要素をそのまま順序付きJSON配列へ保存し、単一対象の各フィールドの代わりにその絶対パスを`repositories`へ渡す
- `previous`、`common_dir`、`registered_worktree`: 対象要素に値がある場合だけ渡す
- `追加指示`: `instruct`のうち対象に関係する部分がある場合だけ渡す
- `引き継ぎ記録先`: 指定の省略や継続時の扱いは`agent-toolkit:delegation`の`references/base-contract.md`「引き継ぎ記録先」に従う

### 受領

実行結果は引数なしの`atk agents wait`で受け取り、本書「リポジトリ担当の返却契約」の固定形式を満たしていることを確認する。
返却の各`対象`を割当集合と突き合わせ、重複・欠落なく各対象の`状態`、`残工程`、`writer_sessions`を「結果と終了」の`result_file`へ保存して読戻す。
続行不能はその対象だけ`blocked`として理由と再開先を残し、他対象の結果は保つ。

<!-- return-contract:repository-process:start -->
<!-- 生成元: share/repository-process.subagent.md「出力」。手動編集しない。 -->
### リポジトリ担当の返却契約

各対象について次の形式のみを返す。5行の組を入力順に繰り返し、結果とwriterは対象別に保持する。
今回進められる作業と処理済み部分の公開・CI検収が完了した場合は`状態`を`completed`とする。
残りが、必要なユーザー判断を終えた有効な未来の冷却期限、未回答UWIへの依存、外部の先行項目への依存で正当に先送りされている場合も含む。
WI自体の完成とは区別し、先送りした項目・残る条件・解除条件・再開先を`残工程`へ記す。
今回進められる作業、対象全体のprocessing、対処を要する不正metadata、未統合レーン、未完了の公開・CI検収があれば`incomplete`、未着手なら`pending`とする。
起動したwriterの終了観測を保ち、欠落・失敗の結果を完了として返さない。

```text
状態: <completed|incomplete|pending>
対象: <target_repo>
残工程: <残る工程と再開先。無ければ「なし」>
writer_sessions: <起動したレーン担当とwriterのsession IDのJSON配列>
結果: <公開結果、CI、処理したAWIと採否の要約>
```

続行不能の場合は`状態:`行の代わりに`../../../share/rules-subagent.md`「返却形式の受け渡し」の`続行できない区分:`と`続行できない理由:`を置き、他の行は保持する。統括担当はこの返却を`result_file`の`blocked`として記録する。
<!-- return-contract:repository-process:end -->

## 結果と終了

初回・再開とも短い起動目的は本スキルの完遂を示す。
今回の集合JSON、追加指示、結果保存先はSessionStartの起動入力から取得する。
目標の達成は本節の最終結果と既存工程の証拠で判断し、回復可能な残工程があれば続行する。
外部前提不足による正当な未完了終了では理由と再開条件を保存し、成功と区別して制御を返す。

処理を開始した担当とwriterの実行識別子を対象別に記録し、終了を観測してから所有の解放と回収へ進む。
`share/wi-submission.md`で永続受付と独立監督への所有移管が成立した投入は、本回の製品writerと区別する。
本回の製品writer・リポジトリ担当、公開CI、結果JSONと報告は本節どおり完了させ、独立投入の終了観測・報告・受付資源の回収は受付先の監督が所有する。
受付票を結果の`independent_submissions`へ保存し、投入が未完了であることを明記する。
単なるPopen成功、委譲session IDの発行、未確認の受付はこの区分へ含めない。
続行できない対象はその対象の理由と再開位置を残し、独立した他の対象は処理する。
次の巡回へ残す工程は、対象・条件・再開先を結果へ保存する。
process-wiの実行全体の完了報告と報告用UWIは統括が1回だけ作成する。
配下のリポジトリ担当は報告用UWIを独立に作らず、公開結果・CI・残工程を統括へ返す。

常駐起動では、会話を終了する前に集合JSONの`result_file`へ次のJSONをUTF-8で保存する。
`repositories`のキーは今回の集合JSONが渡した対象の`target_repo`に限る。明示resumeでも今回の集合だけを書く。値の`status`は`completed`、`incomplete`、`blocked`、`pending`のいずれかとする。
`completed`は今回進められる作業と処理済み部分の公開・CI検収の完了を意味する。
必要な判断を終えて、未来の有効な冷却期限、未回答UWIや外部先行項目への依存で正当に先送りした項目だけが残る場合も含める。
WI自体の完成と区別し、残す項目・未達条件・解除条件・再開先を`remaining`へ記録する。
今回進められる作業、対象全体のprocessing、対処を要する不正metadata、未統合レーン、未完了の公開工程があれば`incomplete`、続行不能なら`blocked`、未着手なら`pending`とする。
`incomplete`は残る未完了工程を表し、終了した統括を同じ会話で自動再起動する要求ではない。
次回へ回す判断をした場合も、実態に合う状態と残工程を保存して今回の統括を終了する。
次の巡回は新しい統括が選定するため、同一会話の再起動を避ける目的だけで冷却期限を追加する必要はない。
内部依存待ち、欠落・失敗の返却、未停止writerを正当な先送りに含めず、writerの終了観測を維持する。
各対象に残工程・再開先・続行不能の理由と、起動した`writer_sessions`の配列を記録する。
リポジトリ担当を開始した直後にもその識別子を保存し、統括の中断後に生存writerを確認できるようにする。
同じ対象を複数の担当が書き込まないよう、配下担当の終了を確かめてから統括自身の作業へ切り替える。

```json
{"repositories":{"example.test/team/repository":{"status":"incomplete","remaining":"CIの検収","writer_sessions":[]}}}
```

報告用UWIの保存と、起動した担当・writerの終了観測を済ませ、最終の`status`・`remaining`・`writer_sessions`を保存する。
`completed`でも正当な延期があれば、対象・未達条件・解除条件・再開先を`remaining`へ列挙する。
保存したJSONを読み戻し、既存の公開・CI・レーンの検収記録と対応することを確認してから、最上位セッションの終了手順へ進み、統括CLIの終了を常駐へ返す。
goalの判定は訂正後の最終保存内容と既存工程の証拠に基づく。
訂正前の記載や正当に延期した項目の存在だけを、当回の到達点の未達としない。
goalのためだけの追加レビューや同じ検証の反復は行わず、不足・不一致がある範囲を補う。
続行不能時も、実態に合う状態、理由と残工程の保存・読戻しを終えて制御を返す。
常駐は結果の申告と実際のキュー・レーン登録が一致することを確認する。
起動済み担当やwriterが未終了の場合は、資源を保持して終了を待機する。

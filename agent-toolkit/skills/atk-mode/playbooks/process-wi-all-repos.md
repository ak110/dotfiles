# 登録全体のAWI処理

登録した全てのリポジトリの今回の対象を、1つの統括会話が受け取って処理し、対象別の結果を保存して終える手順書である。
`atk wi process-loop`が起動した統括（`/atk-mode process-wi all-repos`）が使い、起動入力の集合JSONにある候補・所有・準備済みworktreeを使う。
統括は対象間の配分と新着の受入れを判断し、単位時間あたりのWI処理数を高める。
全対象の処理を終えたら`agent-toolkit:atk-mode/playbooks/process-wi-all-repos-results.md`「結果と終了」へ進み、対象別の結果の保存と終了の観測で終える。
常駐（process-loop）は共有の同期・開始判定と統括会話の起動、対象別の所有・準備・結果の検収・回収を担う。
明示のresumeでは保存済みの会話と未完了の成果を引き継ぎ、元の集合と委譲の契約を保って、未完了の作業を同じ会話で続ける。

## 開始と振り分け

1. 集合JSONを伴わない手動の起動では、`atk wi process-loop --once`を実行して終える。
   この公開操作が全対象の所有・候補ファイル・worktreeを準備し、全対象を担当する1つの統括会話を起動する。
   起動元はCLIの終了まで観測して結果をユーザーへ伝え、対象の作業・完了報告・報告用UWIは起動された統括に任せる。
   対象が0件なら待たずに終わり、未完了・所有待ち・準備失敗は非0で終わる。
   以降の手順は集合JSONを受け取った統括に適用する。
2. 開始時に、`agent-toolkit:atk-mode/playbooks/process-wi-selection.md`「直前の同期結果の検分」の更新ログの確認を、選定と担当の起動と並行して始める。
   対象が複数でも同じ更新の実行の確認は1つにまとめ、結果と処置を全体の進捗記録から参照する。
3. 集合JSONの`repositories`を対象集合とし、各対象の準備済み`worktree`を処理の起点とする。
   `ownership_record`と`ownership_token`は常駐が取得済みの所有であり、配下の担当へそのまま渡す。
   `lane_session_id`は対象ごとに持ち、レーン操作の`--session-id`へ明示する。
4. 対象worktreeの作業に入る前に、その対象の`AGENTS.md`、`CLAUDE.md`、作業に関わるプロジェクトスキルの実在する`SKILL.md`を絶対パスで読んで適用する。
   ホームなどプロジェクト外のcwdでは、対象のプロジェクトスキルを明示的に読み込み、通常のプロジェクトhookが担う確認を自分で行う。
   WorktreeCreateとWorktreeRemoveは、対象の設定を読む既存の`atk`操作で実行する。
5. 統括は受け取った全対象の選定・レビュー・公開・CI・報告・結果の保存を所有し、次のように振り分ける。
   - 初回の担当の起動前に、取得済みの候補本文・件数・readiness・`previous`の残工程から、今回進められる処理の負荷が最大と見込まれる1対象を選び、統括自身が直接担当する。
     変更の規模、検証・公開、再開時の残作業を比べ、差が分からなければ候補数、それも同じなら受け取った順で選び、理由を引き継ぎ記録へ短く残す。
   - 対象が1件なら上限によらず直接処理し、全対象の`lane_limit`が1以下なら統括が全件を処理する。
   - 上限2以上の対象がある複数対象では、直接担当を除く上限1以下の対象群を全てまとめて1つのリポジトリ担当（まとめ担当）へ渡し、残る上限2以上の対象は対象ごとに並行して委譲する。
     軽い残対象がある場合だけまとめ担当を起動し、1件でもその担当へ渡す。
   - `lane_limit`は対象内の並列度と軽い対象の区分だけに使い、負荷最大の対象は上限によらず直接担当に選べる。
   - 統括の直接担当、まとめ担当、重い対象の個別の担当は並行して進める。
6. 直接処理する対象は、`agent-toolkit:atk-mode/playbooks/process-wi.md`「実行順」に従い、`lane_limit`が2以上なら複数レーン、1以下ならsingle-laneで処理する。
   どちらも受け取った候補・readiness・対象worktree・対象別の`lane_session_id`を使い、起動したレーン担当とwriterを対象別の`writer_sessions`へ記録する。
   計画・実行レビュー・公開は対象worktreeごとに分け、開始時のCI点検、監査、UWI、公開とCIの検収も対象ごとに行う。
7. 本書「リポジトリ担当の起動と受領」でまとめ担当と個別の担当を起動する。

## 取得済み候補の受領

集合JSONの`repositories`は対象別のオブジェクトの配列である。

- 各要素は`target_repo`、登録元の`registered_worktree`、準備済みの`worktree`、`common_dir`を持つ。
- 識別子`target`も常に持ち、系統を登録したリポジトリでは`<target_repo>@<系統>`、それ以外では`target_repo`と同じ値である。
  系統を登録したリポジトリの対象は、処理する系統`target_branch`と、系統を省略したWIの宛先の系統`default_branch`も持ち、`agent-toolkit:atk-mode/playbooks/process-wi.md`「系統を登録したリポジトリ」に従って処理する。
- 候補と選定の項目は`candidate_file`、`snapshot_file`、`readiness`、`lane_limit`である。
- 所有と再開の項目は`ownership_record`、`ownership_token`、`lane_session_id`、`previous`である。

1. 開始時の候補は、本文と鮮度を含む`candidate_file`の原本を再取得せずにそのまま使う。
   `pick-wi-check --candidate-file`にはその絶対パスを渡し、受け取ったreadinessの診断も含めて着手の可否を判定する。
   足りない本文と依存先だけを通常のキューのCLIで補う。
2. 受け取った全体作業契約から巡回の開始時刻とタイムゾーンを得て、自分の作業記録へ残す（経過時間の記録に使う）。
3. 新着の確認には受け取った`snapshot_file`（初回の取得と同じ全状態・全種別の識別子の集合）を使い、本文を持つ候補一覧とは別に持つ。
   再開時も初回の集合を引き継ぎ、欠落か破損があれば原本を回復する。
   新着の取得と判定は`agent-toolkit:atk-mode/playbooks/process-wi-immediate-fix.md`「開始後に追加されたWIの確認」に従い、処理を終えた対象の新着も資源に余力があれば同じ実行で処理する。
4. 回答済みのUWI、候補の分類、開始時のCI点検は`agent-toolkit:atk-mode/playbooks/process-wi-selection.md`を使う。
   選定YAMLとNは対象ごとに分け、レーン上限も対象別のNから求める（形式は`agent-toolkit:atk-mode/references/process-wi-selection-format.md`）。
5. 候補が1件であることと、その候補に着手できることは別に確かめる。
   直接処理する場合も、受け取った候補・readiness・対象別の選定・`lane_session_id`の対応を保ち、直接起動したレーン担当とwriterを`writer_sessions`へ記録して、常駐の終了確認・所有の解放・回収と`previous`による再開へ渡す。

## リポジトリ担当の起動と受領

```text
起動対象: repository-process.subagent.md
```

<!-- input-contract:repository-process:start -->
<!-- 生成元: skills/atk-mode/roles/repository-process.subagent.mdのrequired_inputs・optional_inputs。手動編集しない。 -->
入力の定義はこの起動契約とagents_serverの`start`の検査が所有する。
役割固有の入力（`extra_params`）はこの契約の入力一覧で完結する。
通常起動（`mode: delegate`）の共通の準備: 起動の前に`agent-toolkit:atk-mode/references/work-contract.md`に従う全体作業契約を用意し、`extra_params`とは別の公開入力`whole_contract_path`へ絶対パスを渡す。

- 必須入力名: `引き継ぎ記録先`
- 任意入力名: `repositories`、`target_repo`、`worktree`、`candidate_file`、`readiness`、`lane_limit`
- 任意入力名: `ownership_record`、`ownership_token`、`lane_session_id`、`previous`、`common_dir`
- 任意入力名: `registered_worktree`、`target`、`target_branch`、`default_branch`、`追加指示`

<!-- input-contract:repository-process:end -->

### 起動

1. 定型のリポジトリ担当は、agents_serverの`start`をtaskで呼ぶ。
   `model_type`は指定せず、役割文書の`orchestrate`を使う。

   | 引数 | 値 |
   | --- | --- |
   | `cwd` | 単一対象の準備済み`worktree`。まとめ担当ではグループ内の準備済みworktreeの1つ |
   | `subagent_md_path` | `repository-process` |
   | `extra_params` | 次の名前付き入力だけ |

   - 単一対象では、`target_repo`、`worktree`、`candidate_file`、`readiness`、`lane_limit`、`ownership_record`、`ownership_token`、`lane_session_id`に集合JSONの対象要素の同名の値を渡す。
   - まとめ担当では、割り当てる対象要素をそのまま順序付きのJSON配列へ保存し、単一対象の各欄の代わりにその絶対パスを`repositories`へ渡す。
   - `previous`、`common_dir`、`registered_worktree`、`target`、`target_branch`、`default_branch`は、対象要素に値がある場合だけ渡す。
   - `追加指示`は、`instruct`のうち対象に関係する部分がある場合だけ渡す。
   - `引き継ぎ記録先`: 省略と継続時の扱いは`agent-toolkit:delegation/references/base-contract-handoff.md`「引き継ぎ記録先」に従う。
2. 統括は巡回の開始時刻とタイムゾーンを全体作業契約へ記録し、起動時に既存の`whole_contract_path`で渡す。
3. 起動入力の組み方を新しくした回か変えた回に限り、まず1件だけ起動し、担当が入力どおりの対象と候補で処理へ入ったことを確かめてから残りを並行して起動する。
   組み方の誤りを担当1件の費用で見つけるためであり、同じ組み方を繰り返す回は確かめずに並行して起動する。
4. 担当を起動した直後に、そのsession IDを`result_file`の対象別の`writer_sessions`へ保存する（統括が中断した後に生存しているwriterを確かめられるようにするため）。

### 受領

1. 実行結果は引数なしの`atk agents wait`で受け取り、本書「リポジトリ担当の返却契約」の固定形式を満たすことを確かめる。
2. 返却の各`対象`を割当集合と突き合わせ、重複も欠落も無いことを確かめる。
   各対象の`状態`、`残工程`、`writer_sessions`は、`agent-toolkit:atk-mode/playbooks/process-wi-all-repos-results.md`「結果と終了」の`result_file`へ保存して読み戻す。
3. 続行不能はその対象だけ`blocked`として理由と再開先を残し、他の対象の結果は保つ。
   足りない判断・認可・資料・観測・操作をユーザーだけが出せる場合は、返却の契約に従って同じ実行の中でUWIの依存へつなぎ、保存と差し戻しを検収する。

<!-- return-contract:repository-process:start -->
<!-- 生成元: skills/atk-mode/roles/repository-process.subagent.md「出力」。手動編集しない。 -->
### リポジトリ担当の返却契約

各対象について次の形式のみを返す。
5行の組を入力順に繰り返し、結果とwriterは対象別に保持する。
今回進められる作業と処理済み部分の公開・CI検収が完了した場合は`状態`を`completed`とする。
残りが、必要なユーザー判断を終えた有効な未来の冷却期限、未回答UWIへの依存、外部の先行項目への依存で正当に先送りされている場合も含む。
WI自体の完成とは区別し、先送りした項目・残る条件・解除条件・再開先を`残工程`へ記す。
今回進められる作業、対象全体のprocessing、対処を要する不正metadata、未統合レーン、未完了の公開・CI検収があれば`incomplete`、未着手なら`pending`とする。
起動したwriterの終了観測を保ち、欠落・失敗の結果を完了として返さない。

```text
状態: <completed|incomplete|pending>
対象: <target>
残工程: <残る工程と再開先。無ければ「なし」>
writer_sessions: <起動したレーン担当とwriterのsession IDのJSON配列>
結果: <公開結果、CI、処理したAWIと採否の要約>
```

続行不能の場合は`状態:`行の代わりに`agent-toolkit:/share/rules/rules-subagent.md`「返却形式の受け渡し」の`続行できない区分:`と`続行できない理由:`を置き、他の行は保持する。
統括担当はこの返却を`result_file`の`blocked`として記録する。
`user_decision`・`authorization`・`inaccessible_input`の不足をユーザーだけが提供できる場合は、統括担当が同じprocess-wiの実行内で処置する。

処置は`agent-toolkit:atk-mode/playbooks/process-wi-lanes.md`「採否の確定と終了区分」手順3に従う。
統括自身か割当先が、事前承認型UWIを保存して元AWIへ依存を登録し、`inbox`へ差し戻す。
統括は保存結果を検収する。
<!-- return-contract:repository-process:end -->

## 担当の役割

統括は受け取った全対象の判断・確認・完了報告を所有し、統括会話の開始・終了の回収と次回の開始は常駐が所有する（同時に起動する統括会話は1つ）。
統括はレーン上限、共有の検証能力、各対象に固有の終了条件を守る。

1. 要求の中の追加ファイルの編集はレーン担当の判断に任せ、重なりを理由とする事前通知や選定記録の更新は省く。
   競合は対象リポジトリの直列統合で扱う（`agent-toolkit:atk-mode/playbooks/implementation.md`「実装」）。
2. 各担当は自分の対象で作業`process-wi`を完遂し、対象別の結果と残工程を統括へ返す。
   統括は直接担当の処理中も、他の対象からの確認と結果の受領、全体の統括を続ける。
3. 通常の継続では、負荷の変動や軽い対象の終了があっても同じ担当を保つ。
   終了済みの担当から回復する場合だけ、既存の終了観測と所有の移譲の後に、今回の未完了の集合から直接担当を選び直す。
4. 配下の担当の確認事項・担当外の発見・改善点は統括へ届き、報告用UWIは統括が実行全体で1回作る。
   配下の担当は祖先の対話CLIを終える`atk agents-exit-session`を呼ばず、返却と終了観測を統括へ渡す。
   統括自身は対象別の結果を保存した後、最上位の終了手順で統括CLIを終える。
5. 統括は、自分が観測した不良・是正要求・改善点と、配下の担当からの報告へ、`agent-toolkit:atk-mode/playbooks/process-wi-immediate-fix.md`「即時対応」の対処時機の判定を適用する。
   - 受け取った時に処置の根拠と残る前提を検収し、独立した別要求はその時点から起草を進める（投入準備の前倒しは`agent-toolkit:atk-mode/playbooks/process-wi-publishing.md`「公開担当の起動と並行工程」）。
   - 委譲先が不要・担当外とした事象も、統括が根拠と保つべき成果を確かめて処置を決める。
   - 今回追加とした正式改善AWIは対象の担当へ渡し、保存済みの正式名、選定への追加、担当への追送を確かめる。
   - 最小の直接修正の担当・基点・時機は同書「レーンの変更範囲へ加える修正の認可」で決め、統合済みの対象の独立した修正をレーンへ繰り返し加えない。
   - 回避と正式対応AWIだけの報告では、次のセッションで同じ停止が起きない根拠を確かめ、根拠が無ければ書込を担う配下の担当へ直接の修正を指示して検証結果を受け取る。
6. 配下の担当を待つ間の進捗点検では、`agent-toolkit:delegation/references/waiting-and-monitoring-progress.md`「成果物側の観測」に従い、活動の有無と、工程の所要・反復の妥当性を分けて判断する。

## 登録集合

登録集合は、`atk config repos`の`repositories`のうち`process_loop`が有効な項目のローカルworktreeで決まる。
直接編集は`edit=allow`のリポジトリだけで行い、`awi`・`deny`・未登録は`agent-toolkit:atk-mode/playbooks/process-wi-immediate-fix.md`「レーンの変更範囲へ加える修正の認可」の扱いに従う。
検証OSの選定は`agent-toolkit:atk-mode/playbooks/process-wi.md`「検証環境」に従い、登録した`platforms`を各担当へ引き継ぐ。
登録の変更は次回の取得から反映され、処理中の集合は取得時点のまま保たれる。
開始の判定、`update-dotfiles`による更新、`resume`の予約と消費は常駐が行う（`atk wi process-loop --help`）。

結果を保存せずに終えた対象は完了扱いにならず、次の統括は対象別の所有、準備済みworktree、`lane_session_id`と前回の結果を`previous`から引き継ぐ。
統括CLIの終了だけで配下の担当の停止を仮定せず、常駐が実プロセスと登録済みwriterの生存を確かめ、停止が未確認の担当、未統合のレーン、dirtyなworktreeを保つ。
利用上限の時の同じ会話の再開と、予告を受けた統括が先に保存する内容は、`agent-toolkit:atk-mode/references/usage-limit.md`「常駐から起動された統括の保存と再開」に従う。
監査記録は同資料が名指す`docs/development/audit-records-wi.md`にある。
該当の節は「agent-toolkit/skills/atk-mode/playbooks/process-wi-all-repos.md：登録集合：2026年11月12日」である。

## worktreeと資源

常駐は統括の起動前に対象worktreeを準備し、resumeでは既存の準備済みの実体と未完了の成果をそのまま再利用する。
配下のレーンは`agent-toolkit:atk-mode/playbooks/process-wi-lanes.md`の公開`atk`操作で作り、対象別の`lane_session_id`を保つ。
準備・統合・実行レビューは同書と`agent-toolkit:atk-mode/roles/exec.parent.md`へ、公開とCIの検収は`agent-toolkit:atk-mode/playbooks/process-wi-publishing.md`へつなぐ。

## 更新のための本人再開

更新版を使って残工程を続ける必要がある場合は、次の順で行う。

1. 現在の要求・判断・認可、処理対象WI、計画と進捗、成果と未完了の工程、会話ID、稼働版と更新後の版の所在を既存の記録へ保存して読み戻す。
2. 配下のwriterへ保存して終えるよう引き継ぎ、終了を観測する。
   未統合・dirty・使用中の成果は、既存の所有と`lane_session_id`とともに保つ。
   レーン担当や明示的に委譲されたリポジトリ担当は、保存した成果と再開の要求を統括へ返す。
3. 統括本人は未完了の結果を`result_file`へ保存し、自分の会話IDで`atk wi process-loop resume ID`を予約して受理を確かめてから、`atk agents-exit-session`を実行する。
   無人の本人再開には、通常のfinalだけにCLIの終了を任せずにIDを使う。
   元の目的と未完了の要求は、未達成のまま保つ。
4. 常駐は旧CLIとwriterの終了を観測し、更新後の版から予約したIDを再開する。
   再開した本人は保存した記録から要求・認可・残工程を復元し、稼働版を確かめて続け、成功済みの公開・検証は結果に影響する変更や新しい失敗がある範囲だけをやり直す。

## 所有・継続・回収

1. 担当を起動する前に、受け取った`previous`と前回の結果ファイルから、ユーザーだけが出せる入力の不足で`blocked`となったAWIの現状を確かめる。
   未回答のUWIへの依存が未登録なら、`agent-toolkit:atk-mode/playbooks/process-wi-lanes.md`「採否の確定と終了区分」手順3のUWIの保存・依存の登録・`inbox`への差し戻しを先に行う。
   自分で調達できる入力の不足はこの回答待ちに含めず、通常の調査と再開へ渡す。
2. 所有は対象（`target`が示すoriginと系統の組）ごとで、所有待ちや準備失敗の対象を除いて独立した対象の処理を続ける。
   台帳に無いIDの明示resumeと会話選択画面からの再開では、起動入力から受け取った実IDとcwdに対応する対象の所有を確定してから元の作業を続け、選ばれなかった対象へ結果を転用しない。
3. 統括と配下のwriterの終了を確かめた対象だけ所有を解放し、既存のlaneを回収する（CLIの終了と書込担当の終了は別に観測する）。
   停止が未確認なら所有を保ち、未統合・dirty・使用中・回収失敗の資源を保つ。
   回収の結果と理由をログへ残し、理由を解消して`atk lane delete --session-id <lane_session_id>`で判定し直す。
   候補の原本と再開情報は検収まで保つ。
4. `abort`、`abort-cancel`、`status`、`instruct`、`instruct-cancel`、`resume`、`resume-cancel`はOSアカウントごとに保たれる。
   `instruct`は次に実際に起動する1会話へ渡り、そのメインが対象に関係する部分を適用する。

返す報告に含める項目: 直接担当とまとめ担当・個別担当の割当と理由、対象ごとの受領した返却、`agent-toolkit:atk-mode/playbooks/process-wi-all-repos-results.md`が定める結果と終了の項目。

# 登録全体のAWI処理

目的: 統括が登録リポジトリごとに担当を決めて結果を検収し、ユーザーが対象ごとに起動しなくても全対象の成果を欠かさず受け取れるようにする。
登録リポジトリの対象別の配分、候補の受け渡し、担当の検収を扱う。
連続巡回の選定と区切りの運用は `agent-toolkit:atk-mode/playbooks/process-loop.md` が定める。
手動の `process-wi all-repos` は登録対象を1回処理し、結果の検収と報告で終える。
明示の再開では保存済みの会話と成果を使い、同じ作業をやり直さない。

## 開始と振り分け

1. 統括は本書「登録集合」から対象と系統を確定する。
   同じ会話の再開では、取得済みの対象一覧・候補・所有を再利用する。
   初回は各対象の登録元を直接編集せず、本書「worktreeと資源」で残存登録と稼働中のwriterを調べる。
   同じ対象と系統を使用中なら保留し、空いている対象は固有手順か既存のWorktreeCreateで系統の先端から専用作業ツリーを準備する。
2. 開始時に、`agent-toolkit:atk-mode/playbooks/process-wi-selection.md`「直前の同期結果の検分」の更新ログの確認を、選定と担当の起動と並行して始める。
   対象が複数でも同じ更新の実行の確認は1つにまとめる。
3. 初回だけ、統括が全対象で共通の `atk wi pull` を1回行う。
   その後、対象ごとの一覧では `--pull` の代わりに `--skip-pull` を指定し、同じ取得結果を使う。
   初回は対象の作業ツリーで`agent-toolkit:atk-mode/playbooks/process-wi-selection.md`「処理対象の決定」の初回取得を`--jsonl`で行い、候補の出力と開始時集合をそれぞれ`candidate_file`と`snapshot_file`へ保存する。
   開始条件の診断と対象ごとの候補・作業ツリーを `repositories` の順序付きJSON配列へ記録する。
   対象一覧と両方の保存先の絶対パスを統括の引き継ぎ記録に残す。
   各対象の書き込み担当を重ねず、選定から検収まで同じ作業ツリーを使う。
   `lane_session_id`は対象・系統ごとに別の識別子を初回に割り当て、既存の所有から再開する時はその値を保ち、レーン操作の`--session-id`へ明示する。
4. 対象worktreeの作業に入る前に、その対象の`AGENTS.md`、`CLAUDE.md`、作業に関わるプロジェクトスキルの実在する`SKILL.md`を絶対パスで読んで適用する。
   ホームなどプロジェクト外のcwdでは、対象のプロジェクトスキルを明示的に読み込み、通常のプロジェクトhookが担う確認を自分で行う。
   WorktreeCreateとWorktreeRemoveは、対象の設定を読む既存の`atk`操作で実行する。
5. 統括は受け取った全対象の選定・レビュー・公開・CI・報告を所有し、次のように振り分ける。
   - 初回の担当の起動前に、取得済みの候補本文・件数・readiness・再開時の残工程から、今回進められる処理の負荷が最大と見込まれる1対象を選び、統括自身が直接担当する。
     変更の規模、検証・公開、再開時の残作業を比べ、差が分からなければ候補数、それも同じなら受け取った順で選び、理由を引き継ぎ記録へ短く残す。
   - 対象が1件なら上限によらず直接処理し、全対象の`lane_limit`が1以下なら統括が全件を処理する。
   - 上限2以上の対象がある複数対象では、直接担当を除く上限1以下の対象群を全てまとめて1つのリポジトリ担当（まとめ担当）へ渡し、残る上限2以上の対象は対象ごとに並行して委譲する。
     軽い残対象がある場合だけまとめ担当を起動し、1件でもその担当へ渡す。
   - `lane_limit`は対象内の並列度と軽い対象の区分だけに使い、負荷最大の対象は上限によらず直接担当に選べる。
   - 統括の直接担当、まとめ担当、重い対象の個別の担当は並行して進める。
6. 直接処理する対象は、`agent-toolkit:atk-mode/playbooks/process-wi.md`「実行順」に従い、`lane_limit`が2以上なら複数レーン、1以下ならsingle-laneで処理する。
   どちらも受け取った候補・readiness・対象worktree・対象別の`lane_session_id`を使う。
   計画・実行レビュー・公開は対象worktreeごとに分け、開始時のCI点検、監査、UWI、公開とCIの検収も対象ごとに行う。
7. 本書「リポジトリ担当の起動と受領」でまとめ担当と個別の担当を起動する。
   初回の候補と配分は保ち、担当の成果の受領・統合と公開単位の検収で、新着や未着手を空いた担当へ追加できるかを確かめる。
   時機は[開始後に追加されたWIの確認](process-wi-immediate-fix.md#開始後に追加されたwiの確認)で決める。

## 取得済み候補の受領

`repositories`は対象別のオブジェクトの配列である。

- 各要素は`target_repo`、登録元の`registered_worktree`、準備済みの`worktree`、`common_dir`を持つ。
- 識別子`target`も常に持ち、系統を登録したリポジトリでは`<target_repo>@<系統>`、それ以外では`target_repo`と同じ値である。
  系統を登録したリポジトリの対象は、処理する系統`target_branch`と、系統を省略したWIの宛先の系統`default_branch`も持ち、`agent-toolkit:atk-mode/playbooks/process-wi.md`「系統を登録したリポジトリ」に従って処理する。
- 候補と選定の項目は`candidate_file`、`snapshot_file`、`readiness`、`lane_limit`である。
- 対象別のレーン操作の識別子は`lane_session_id`に置く。

1. 開始時の候補は、本文と鮮度を含む`candidate_file`の原本を再取得せずにそのまま使う。
   `pick-wi-check --candidate-file`にはその絶対パスを渡し、受け取ったreadinessの診断も含めて着手の可否を判定する。
   足りない本文と依存先だけを通常のキューのCLIで補う。
2. 新規と既存の区別には受け取った`snapshot_file`（初回の取得と同じ全状態・全種別の識別子の集合）を使い、本文を持つ候補一覧とは別に持つ。
   再開時も初回の集合を引き継ぎ、欠落か破損があれば原本を回復する。
   新着と新たに処理可能になった既存WI、内部依存待ちの追加候補の取得・判定は`agent-toolkit:atk-mode/playbooks/process-wi-immediate-fix.md`「開始後に追加されたWIの確認」に従う。
   常駐の巡回では、処理を終えた対象も含め、再開直後・30分点検・成果の受領や統合・公開単位の検収で追加・再開候補を確認する。
   既存レーンへ加えられる候補を先に扱い、新設にだけレーン数の空きを求める。
3. 回答済みのUWI、候補の分類、開始時のCI点検は`agent-toolkit:atk-mode/playbooks/process-wi-selection.md`を使う。
   選定YAMLとNは対象ごとに分け、レーン上限も対象別のNから求める（形式は`agent-toolkit:atk-mode/references/process-wi-selection-format.md`）。
4. 候補が1件であることと、その候補に着手できることは別に確かめる。
   直接処理する場合も、受け取った候補・readiness・対象別の選定・`lane_session_id`の対応を保ち、直接起動したレーン担当の終了を統括の終了確認と回収で確かめる。

## リポジトリ担当の起動と受領

```text
起動対象: repository-process.subagent.md
```

<!-- input-contract:repository-process:start -->
<!-- 生成元: skills/atk-mode/roles/repository-process.subagent.mdのrequired_inputs・optional_inputs。手動編集しない。 -->
入力の定義はこの起動契約とagents_serverの`start`の検査が所有する。
役割固有の入力（`extra_params`）はこの契約の入力一覧で完結する。
起動の準備で`<役割名>.subagent.md`の本文は読まず、入力はこの一覧で決め、形式の確認は`start`に任せる。
通常起動の前に`agent-toolkit:atk-mode/references/work-contract.md`に従う全体作業契約を用意する。
`agents_server.start`に役割文書の`subagent_md_path`と、以下の役割固有の入力をまとめた`extra_params`を渡す。
`mode`は省略する（明示する場合は`mode: task`）。
全体作業契約の絶対パスは`extra_params`の外の`whole_contract_path`に渡す。

- 必須入力名: `引き継ぎ記録先`
- 任意入力名: `repositories`、`target_repo`、`worktree`、`candidate_file`、`snapshot_file`、`readiness`
- 任意入力名: `lane_limit`、`lane_session_id`、`common_dir`、`registered_worktree`、`target`、`target_branch`
- 任意入力名: `default_branch`、`追加指示`

<!-- input-contract:repository-process:end -->

### 起動

1. 定型のリポジトリ担当は、agents_serverの`start`をtaskで呼ぶ。
   `model_type`は指定せず、役割文書の`execute`を使う。
   本人の今回の指定がCodexに限定する場合に限り、`atk config get execute_model`のCodex候補だけを設定順のまま渡す。
   `model_override=user_instruction`と[本人指定の根拠](../references/model-settings.md#本人指定による上書き)を添え、候補が無ければ起動しない。
   終了した旧担当へ今回の指定を`send_message`で渡せない時は、同じ指定を`handoff_from`で照合し直し、後任にだけ新しい指示を送る。

   | 引数 | 値 |
   | --- | --- |
   | `cwd` | 単一対象の準備済み`worktree`。まとめ担当ではグループ内の準備済みworktreeの1つ |
   | `subagent_md_path` | `repository-process` |
   | `extra_params` | 次の名前付き入力だけ |

   - 単一対象では、`target_repo`、`worktree`、`candidate_file`、`snapshot_file`、`readiness`、`lane_limit`、`lane_session_id`に対象一覧の同名の値を渡す。
   - まとめ担当では、割り当てる対象要素をそのまま順序付きのJSON配列へ保存し、単一対象の各欄の代わりにその絶対パスを`repositories`へ渡す。
   - `common_dir`、`registered_worktree`、`target`、`target_branch`、`default_branch`は、対象要素に値がある場合だけ渡す。
   - `追加指示`は、`instruct`のうち対象に関係する部分がある場合だけ渡す。
   - `引き継ぎ記録先`: 省略と継続時の扱いは`agent-toolkit:atk-mode/references/delegation-prompt.md`「引き継ぎ記録先」に従う。
2. 統括は起動時に現行の作業契約を既存の`whole_contract_path`で渡す。
3. 起動入力の組み方を新しくした回か変えた回に限り、まず1件だけ起動し、担当が入力どおりの対象と候補で処理へ入ったことを確かめてから残りを並行して起動する。
   組み方の誤りを担当1件の費用で見つけるためであり、同じ組み方を繰り返す回は確かめずに並行して起動する。

### 受領

1. 実行結果は引数なしの`atk agents wait`で受け取り、本書「リポジトリ担当の返却契約」の固定形式を満たすことを確かめる。
2. 返却の各`対象`を割当集合と突き合わせ、重複も欠落も無いことを確かめる。
   `結果`に含まれる判断は、`agent-toolkit:atk-mode/playbooks/lane-receipt.md`「実測と判断の検収」に従って原本から確かめる。
   根拠の所在が分からなければ同じ担当に求め、結論を確かめた対象から記録する。
   各対象の`状態`と`残工程`は、`agent-toolkit:atk-mode/playbooks/process-wi-all-repos-results.md`「結果と終了」の対象別の結果として報告に使う（ファイルへは保存しない）。
3. 続行不能はその対象だけ`blocked`として理由と再開先を残し、他の対象の結果は保つ。
   足りない判断・認可・資料・観測・操作をユーザーだけが出せる場合は、返却の契約に従って同じ実行の中でUWIの依存へつなぎ、保存と差し戻しを検収する。

4. 対象別の最終返却を検収し、その担当を再開しないと決めたら、担当の `session_id` を指定してagents_serverの `stop` で破棄する。
   中間の返却では破棄しない。

<!-- return-contract:repository-process:start -->
<!-- 生成元: skills/atk-mode/roles/repository-process.subagent.md「出力」。手動編集しない。 -->
### リポジトリ担当の返却契約

各対象について次の形式のみを返す。
4行の組を入力順に繰り返し、結果は対象別に保持する。
今回進められる作業と処理済み部分の公開・CI検収が完了した場合は`状態`を`completed`とする。
残りが、必要なユーザー判断を終えた有効な未来の冷却期限、未回答UWIへの依存、外部の先行項目への依存で正当に先送りされている場合も含む。
WI自体の完成とは区別し、先送りした項目・残る条件・解除条件・再開先を`残工程`へ記す。
今回進められる作業、対象全体のprocessing、対処が必要な不正metadata、未統合レーン、未完了の公開・CI検収があれば`incomplete`、未着手なら`pending`とする。
起動したwriterの終了観測を保ち、欠落・失敗の結果を完了として返さない。

```text
状態: <completed|incomplete|pending>
対象: <target>
残工程: <残る工程と再開先。無ければ「なし」>
結果: <公開結果、CI、処理したAWIと採否の要約>
```

続行不能の場合は`状態:`行の代わりに`agent-toolkit:atk-mode/playbooks/subagent.md`「返却形式の受け渡し」の`続行できない区分:`と`続行できない理由:`を置き、他の行は保持する。
統括担当はこの返却を、その対象の`blocked`として完了報告に載せる。
`user_decision`・`authorization`・`inaccessible_input`の不足をユーザーだけが提供できる場合は、統括担当が同じprocess-wiの実行内で処置する。

処置は`agent-toolkit:atk-mode/playbooks/process-wi-lanes.md`「採否の確定と終了区分」手順3に従う。
統括自身か割当先が、事前承認型UWIを保存して元AWIへ依存を登録し、`inbox`へ差し戻す。
統括は保存結果を検収する。
<!-- return-contract:repository-process:end -->

## 担当の役割

統括は受け取った全対象を判断・確認し、担当の結果を検収して完了を報告する。
統括はレーン上限、共有の検証能力、各対象に固有の終了条件を守る。

1. 要求の中の追加ファイルの編集はレーン担当の判断に任せ、重なりを理由とする事前通知や選定記録の更新は省く。
   競合は対象リポジトリの直列統合で扱う（`agent-toolkit:atk-mode/playbooks/implementation.md`「実装」）。
2. 各担当は自分の対象で作業`process-wi`を完遂し、対象別の結果と残工程を統括へ返す。
   担当は実測の開始時から`agent-toolkit:atk-mode/playbooks/lane-receipt.md`「実測と判断の検収」に従って判断と根拠を記録し、返却の`結果`に原本の所在を示す。
   統括は直接担当の処理中も、他の対象からの確認と結果の受領、全体の統括を続ける。
3. 通常の継続では、負荷の変動や軽い対象の終了があっても同じ担当を保つ。
   終了済みの担当から回復する場合だけ、既存の終了観測と所有の移譲の後に、今回の未完了の集合から直接担当を選び直す。
4. 配下の担当の確認事項・担当外の発見・改善点は統括へ届き、報告用UWIは統括が実行全体で1回作る。
   配下の担当は祖先の対話CLIを終える`atk agents-exit-session`を呼ばず、返却と終了観測を統括へ渡す。
   リポジトリ担当は着手時に`agent-toolkit:atk-mode/playbooks/main-reporting.md`を読み、同書の報告と点検を統括へ向けて行う。
   統括は対象別の結果を確定し、巡回を続ける場合は巡回手順の区切りに進む。
5. 統括は、自分が観測した不良・是正要求・改善点と、配下の担当からの報告へ、`agent-toolkit:atk-mode/playbooks/process-wi-immediate-fix.md`「即時対応」の対処時機の判定を適用する。
   - 受け取った時に処置の根拠と残る前提を検収し、独立した別要求はその時点から起草を進める（投入準備の前倒しは`agent-toolkit:atk-mode/playbooks/process-wi-publishing.md`「公開担当の起動と並行工程」）。
   - 委譲先が不要・担当外とした事象も、統括が根拠と保つべき成果を確かめて処置を決める。
   - 今回追加とした正式改善AWIは対象の担当へ渡し、保存済みの正式名、選定への追加、担当への追送を確かめる。
   - 最小の直接修正の担当・基点・時機は同書「レーンの変更範囲へ加える修正の認可」で決め、統合済みの対象の独立した修正をレーンへ繰り返し加えない。
   - 回避と正式対応AWIだけの報告では、次のセッションで同じ停止が起きない根拠を確かめ、根拠が無ければ書き込みを担う配下の担当へ直接の修正を指示して検証結果を受け取る。
6. 配下の担当を待つ間の進捗点検では、`agent-toolkit:atk-mode/playbooks/delegation-waiting.md`「成果物側の観測」に従い、活動の有無と、工程の所要・反復の妥当性を分けて判断する。

## 登録集合

登録集合は、`atk config repos`の`repositories`のうち`process_loop`が有効な項目のローカルworktreeで決まる。
直接編集は`edit=allow`のリポジトリだけで行い、`awi`・`deny`・未登録は`agent-toolkit:atk-mode/playbooks/process-wi-immediate-fix.md`「レーンの変更範囲へ加える修正の認可」の扱いに従う。
検証OSの選定は`agent-toolkit:atk-mode/playbooks/process-wi.md`「検証環境」に従い、登録した`platforms`を各担当へ引き継ぐ。
登録の変更は次回の取得から反映され、処理中の集合は取得時点のまま保たれる。
開始の判定と更新は巡回のセッションで行い、ランチャーは予約を消費して起動する。

予約なしの異常終了で、完了を報告せずに終えた対象は完了扱いにならず、次の統括は残っているレーンの所有登録、準備済みworktree、WIキューの状態から引き継ぐ。
統括CLIの終了だけで配下の担当の停止を仮定せず、統括が`atk agents list`と実プロセスで配下の担当の生存を確かめ、停止が未確認の担当、未統合のレーン、dirtyなworktreeを保つ。
利用上限の時の同じ会話の再開と、予告を受けた統括が先に保存する内容は、`agent-toolkit:atk-mode/references/usage-limit.md`「常駐から起動された統括の保存と再開」に従う。

## worktreeと資源

再開では既存の作業ツリーと未完了の成果を再利用する。
対象の準備前に、[レーン資源の回収](process-wi-lane-resources.md)「レーン資源の作成・再利用・回収」の手順1・2で、前回の残存資源を回収する。
配下のレーンは`agent-toolkit:atk-mode/playbooks/process-wi-lanes.md`の公開`atk`操作で作り、対象別の`lane_session_id`を保つ。
準備・統合・実行レビューは同書と`agent-toolkit:atk-mode/roles/exec.parent.md`へ、公開とCIの検収は`agent-toolkit:atk-mode/playbooks/process-wi-publishing.md`へつなぐ。

## 更新のための本人再開

dotfilesの公開反映、本体・規範の更新や20時間点検で統括が自分の会話IDを予約して再起動する場合だけ、配下のレーンを稼働させたまま次の手順で再開する。
プラグインの公開・再読み込みとホスト本体の版の切り替えは別に判定する。

対象は、`atk agents-exit-session` で終了できるClaudeの統括（OSを問わない）と、Linuxの対話端末で動くCodex・Antigravityの統括である。
それ以外では `unsupported` で統括は終了しない。
従来どおり配下writerへ保存して終えるよう引き継ぎ、終了を観測してからユーザーの操作か既存の手段で終了する。

1. 新しい作業の開始を保留し、統合済み（`phase: finalized`）のレーンの変更だけを、[既存の公開工程](process-wi-publishing.md)の公開単位で公開・CI検収まで進める。
   dotfilesの公開反映に伴う本人再開では、検収済みの公開を保ち、追加の公開は始めない。
   未統合のレーンの終了を待たない。
2. 現在の要求・判断・認可、処理対象WI、計画と進捗、公開・検証の結果と残工程、会話ID、稼働版と更新後の版の所在を引き継ぎ記録に保存する。
   受領待ちの各レーンについて、担当の `session_id`、`lane_session_id`、対象worktree、選定結果の所在、次に受領する工程も保存し、読み戻す。
   [前区間の振り返り](process-loop.md#前区間の振り返り)の境界も既存の作業契約の引き継ぎに残す。
3. `atk wi process-loop resume <自分の会話ID>` の受理を確認してから `atk agents-exit-session` を実行する。
   配下のwriterの終了を求めず、通常終了の所有解放と実行中・未統合のレーン資源の回収は行わない。
4. 同じ会話IDで再開したら、引き継ぎ記録と `atk agents list` を比べ、[レーンの復元手順](process-wi-lanes.md#各段階の全初期通常レーン起動後の会話圧縮)から同じ待機対象を復元する。
   到着済みの上り通知と未受領の結果を回収し、結果のない稼働中レーンは待機対象に残す。
   まだ結果がなければここで待たず、選定へ進む。
   状態確認・中断・追送には既存の `show`・`kill`・`send_message` を使う。
   AntigravityとOpenCodeのレーンへの追送はturn終了後に行う。
5. 稼働版を確認し、成功済みの公開・検証を再利用して残工程を続ける。
   dotfilesの公開反映からの再開では、記録したOIDに対して[巡回する](process-loop.md#巡回する)の手順5の検収を終えてから次の公開へ進む。
   再開直後の追加候補の確認には「取得済み候補の受領」を使う。
   統括とレーンのmanaged-tempおよびworktreeの分離は変えない。

## 所有・継続・回収

1. 担当を起動する前に、WIキューと依存の状態から、ユーザーだけが出せる入力の不足で`blocked`となったAWIの現状を確かめる。
   未回答のUWIへの依存が未登録なら、`agent-toolkit:atk-mode/playbooks/process-wi-lanes.md`「採否の確定と終了区分」手順3のUWIの保存・依存の登録・`inbox`への差し戻しを先に行う。
   自分で調達できる入力の不足はこの回答待ちに含めず、通常の調査と再開へ渡す。
2. 所有は対象（`target`が示すoriginと系統の組）ごとで、所有待ちや準備失敗の対象を除いて独立した対象の処理を続ける。
   明示resumeと会話選択画面からの再開では、実際のworktreeと残っているwriterを確かめ、対象の所有を確定してから元の作業を続け、選ばれなかった対象へ結果を転用しない。
3. 「更新のための本人再開」は通常終了に含めない。
   単回実行、abort・ユーザーの中断、会話を替える交代では、配下のwriterと公開の結果を回収し、通常終了の所有を解放して資源を回収する。
   通常終了では、配下のwriterが停止し、統括自身も対象への書き込みを終えたことを確かめてから、統括がセッション終了前に所有を解放する。
   対象ごとに `atk lane delete --session-id <レーン所有session> --repo <統合先worktree>` で回収する。
   異常終了で残った資源は、次の統括が本書「worktreeと資源」に従って判定する。
   完成結果とwriterの停止を確認した通常の終了では、選定が未統合でもbranchの変更が統合先にあれば回収する。
   続行予定、停止が未確認、dirty、使用中、未取込み、回収失敗の資源は保持する。
   回収の結果と理由をログへ残し、理由を解消したら [レーン資源の回収](process-wi-lane-resources.md) に従って元の判定モードで再試行する。
   候補の原本と再開情報は検収まで保つ。
4. `abort`、`abort-cancel`、`status`、`instruct`、`instruct-cancel`、`resume`、`resume-cancel`はOSアカウントごとに保たれる。
   `instruct`は次に実際に起動する1会話へ渡り、そのメインが対象に関係する部分を適用する。

返す報告に含める項目: 直接担当とまとめ担当・個別担当の割当と理由、対象ごとの受領した返却、`agent-toolkit:atk-mode/playbooks/process-wi-all-repos-results.md`が定める結果と終了の項目。

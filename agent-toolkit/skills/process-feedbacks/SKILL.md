---
<!-- 設計意図: docs/development/design.md の「終端工程」を参照。 -->
name: process-feedbacks
description: >
  対象リポジトリのフィードバックを取得・検討・適用するときに起動する。
  「フィードバックがあった」「改善提案を反映」「振り返り結果を反映」などのキーワードで起動する。
---

# フィードバック処理

本スキルは、フィードバック処理を完遂する手順を提供する。
activeなフィードバックを取得し、調査、採否、実装、公開、後始末まで完遂する。
新規フィードバックとTBDの投入は`agent-toolkit:add-feedback`を正本とし、本スキルへ投入手順を複製しない。

本スキルの起動中は自律モードとする。ユーザー判断が必要な事項と確認必須の例外は
`agent-toolkit/rules/01-agent.md`「協調と自律」節に従い、TBDへ永続化して暫定判断で進める。

## 1. 入力とreadiness

対象リポジトリの絶対パスを確定し、次を行う。

1. `atk mq list --status=active --target-repo=<repo-path>`でactive項目を取得する。
   `CLAUDECODE`が設定されている場合は、この一覧のfilenameを本セッションの処理対象として固定する。
   起動goalにCodexオーケストレーターの連続処理と明記されている場合（以下、連続処理モード）は、
   後述のprocess-loop用再取得も適用する
2. 必要なfilenameだけを`atk mq show <filename> --target-repo=<repo-path>`で取得する
3. `plan_file`を持つfeedbackを計画実装型、それ以外を通常型とする。本文から型を推測しない
4. 本文の順序条件はreadiness判定前に抽出し、active項目から対象filename自身を除外した項目を依存先候補とする。候補を追加した依存グラフに自己依存又は循環が無いことを登録前に検査し、該当時は登録せず順序条件をTBDへ送る。検査を通過した候補だけを`atk mq set-dependencies <filename> --depends-on=<filename> ... --target-repo=<repo-path>`へ登録する。`--depends-on`を付けない実行は依存の全解除となるため使用しない。保存結果を照合する
5. `depends_on`が全て終端し、TBDは回答済みで、frontmatterと計画ファイルが有効な項目をreadyとする
6. readyなinbox項目を`atk mq start-processing`でprocessingへ移す。
   processing項目は実体を確認し、完了済み工程を再実行せず未完了工程から再開する

`start-processing`が状態競合で拒否した場合は、active一覧と必要な本文を再取得し、readiness判定から再開する。

欠落依存、自己依存、循環、不正な`cooldown_until`、frontmatter破損、計画ファイル消失は修復対象とする。
過去の`queue_schedule.dependency`は読取互換だけ維持し、新規記録へ用いない。
計画実装型を1件以上扱う場合は`references/plan-impl-feedback-flow.md`を全文読む。

## 2. 調査と採否

複数項目を連続処理する場合、新しい項目の調査は前項目の調査結果・仮説・識別子を引き継がず
独立に確定する（努力目標）。

Claude Codeホストでは`references/feedbacks-planner-reception.md`を全文読み、active一覧を取得した時点のreadyな通常型項目を
1 waveとして1つの`agent-toolkit:feedbacks-planner`へ渡す。
本文はメインが`atk mq show`で取得して渡し、plannerは再取得しない。
調査と計画工程は対象worktreeを読み取り専用で共有し、項目別worktreeを作成しない。
readyな計画実装型laneは通常型waveの計画工程を待たず、利用可能なwriter枠で実装できる。

サブエージェント機能を利用できないCodexホストでは、通常型を次の順で扱う。

1. `references/content-adjustment.md`と`references/review-checklists.md`を全文読む
2. 原文、現行実装、関連規範、履歴、既存の成功経路を調査する
3. バグ・障害・回帰では実行主体が`agent-toolkit:bugfix`をSkill機能で起動する
4. 横断調査を委譲する場合は`agent-toolkit:delegation`に従い、
   `references/explore-template.md`だけを受信者用taskとして渡す
5. 単一要求は採用または不採用、独立した複数要求は採用、部分採用、不採用で判定する。
   原文の単一要求を薄めて部分採用しない
6. `references/decision-format.md`に従って採否と根拠を記録する

外部ツール、ライブラリ、サービスの挙動を成果物へ転記する前に、一次資料または実装で裏付ける。
技術的に確定できない事項とユーザー判断は保留へ送る。

## 3. 保留

保留時は`references/hold-with-tbd-inject.md`を全文読み、解除条件と再開情報を永続化する。
filenameで表せない、ユーザー判断を伴わない外部条件待ちは本文へ観測方法、現在値、解除条件、再開工程を記録し、
`atk mq return-to-inbox <filename> --cooldown-days=3`でinboxへ戻す。外部条件に応じて3日より長い日数も指定できる。
別feedback待ちは`depends_on`、ユーザー判断待ちはTBDと通常の`atk mq return-to-inbox`を使い、cooldownを重ねない。
回答済みTBDの能動的なpoll、内部待機ループ、同一セッションでの時限待機は行わない。
TBDの回答が単純な回答を超える指示・是正要求を含む場合は`agent-toolkit:bugfix`の深掘り条件を判定し、
自律モードの原則（TBD記録と暫定判断での続行）に従って進める。

readyな採用項目が無ければ、保留状態を維持して「6. 振り返りと終了」へ進む。
process-loopがactive状態の変化を検出し、readiness成立後に新しいセッションを起動する。

## 4. 実装と公開

本文を実装要求と、commit作成以降の終端工程（push特別指定、PR/MR、リリース、タグ付け、配布及び公開）へ分離する。
終端工程はlane又は統合writerへ委譲しない。
終端工程を持つ項目は、公開する変更集合、公開先及びPR/MR操作集合を本文の明示記載から一意に確定し、不明な要素はTBDへ送る。
順序条件とPR/MR指定を併せ持つ項目を検出した直後、公開グループの操作前に`references/publish-group.md`を全文読む。
実装commitをpushする全経路でpush済みOIDのCI通過を確認し、lane commitを未公開のままadoptしない。
PR/MRの作成、マージ又は作成＋マージ、リリースは、全laneの統合、push及びCI通過の後、adoptの前にメインが1回だけ実行する。
本文に明記されない不可逆操作はTBDへ送る。
失敗時はpush済み内容を巻き戻さず、実施済み工程と観測内容を記録したTBDを投入して`atk mq return-to-inbox`で保留する。
終端工程だけを求める項目は計画を作成せず、終端待機集合へ登録して全laneの統合とpush後にfilename昇順で実行する。

- Claude Codeホストの通常型採用項目は、plannerの統合計画を各feedbackへ`atk mq convert-to-plan`で記録し、
  `references/plan-impl-feedback-flow.md`の計画実装型経路へ移行する
- Codexホストの通常型採用項目は実行主体が`agent-toolkit:plan-mode`をSkill機能で起動し、調査済み事実と採否を渡す
- 計画実装型は`references/plan-impl-feedback-flow.md`に従い、計画ファイルを正本として実装する
- commit前に実行主体が`agent-toolkit:commit`をSkill機能で起動する
- 実装と二系統reviewの完了後、呼び出し元がpushとCI通過確認を完遂する
- 計画の完了条件を満たした対象だけを後始末へ進める

メインはqueue操作、planner・executor・統合スレッドの起動と検収、TBDと新規feedbackの投入、
上流取得、統合worktreeの作成と回収、push、CI通過確認を担当する。
lane commitの適用、競合解消、履歴一本化、検証は統合writerへ委譲する。
統合writerの新規起動又は継続接続の直前に`atk config get merge_model`で経路を解決し、
`references/merge-task.md`を渡す。

作業中に独立した新規改善を発見した場合は、実行主体が`agent-toolkit:add-feedback`をSkill機能で起動し、
完成済み本文と対象リポジトリを渡す。投入コマンドを本スキルから直接構成しない。

## 5. 後始末

- 不採用: 判定確定後に`atk mq reject <filename> --note=<理由>`を実行する
- 採用: 終端工程を持つ項目は全終端工程の成功後、持たない項目は対象commitのpushとCI通過確認後に
  `atk mq adopt <filename> --note=<反映概要> --commit=<完全長SHA>`を実行する
- 回答済みTBD: 回答を反映した処理の完了後に同じ採用経路で終端させる

`adopt`と`reject`は、同じ対象リポジトリに回答済みのactive TBDが残る場合、
queue lock内の変異直前検査で停止する。通知の有無を処理可否の根拠にしない。
各コマンドの保存結果を再取得し、対象、採否、note、commitを照合する。
終端工程を持つ項目のnoteには実施した操作と結果を記録する。

後始末の完了後は再取得したready項目の有無で分岐し、ready項目があれば「2. 調査と採否」へ戻り、
ready項目が無い場合だけ「6. 振り返りと終了」へ進む。再取得の範囲だけがホストで異なる。
Claude Codeでは起動時に固定した未終端項目だけを再取得し、起動後に追加された項目はactiveのまま残して
process-loop又は次回の手動起動による新しいセッションで扱う。
Codexでは実装と後始末の間にactive一覧を再取得し、追加分を含むready項目も対象とする。
連続処理モードでは、
取得済みのready項目を終端させたか保留した後にactive一覧を再取得し、
依存関係の有無を問わず追加分を含むready項目を対象とする。
「6. 振り返りと終了」の開始後に追加された項目は次回の手動起動で扱う。
既に本セッションのcommitで要求が満たされている場合は、その実測を根拠に採用として終端させる。

## 6. 振り返りと終了

全ready項目の処理又は保留永続化後に、実行主体が`agent-toolkit:session-review`をSkill機能で起動する。
振り返りで生成した提案は同スキルがadd-feedbackを起動して投入する。完了後に実行主体が
`agent-toolkit:exit-session`をSkill機能で起動する。

`exit-session`が到達できる終了の範囲は実行ホストによって異なる。
本体プロセスの停止を要求できないホストでは、終了理由を最終応答としてターンを完了させた時点で
終了工程が正常に完了したものとして扱う。プロセスの停止と次セッションの起動はホスト側の責務とする。

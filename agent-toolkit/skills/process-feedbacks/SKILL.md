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

本スキルの起動中は自律モードとする。ユーザー判断が必要な事項は
`agent-toolkit/rules/01-agent.md`「協調と自律」節に従って`AskUserQuestion`で確認し、
同節が定める契機（回答期限の超過、又は期限を提供しない実行環境では応答を得られないと判断した時点）で
TBDへ永続化して暫定判断で進める。

## 1. 入力と着手可否

対象リポジトリの絶対パスを確定し、次を行う。

1. `atk mq list --status=active --target-repo=<repo-path>`でactive項目を取得する。
   `CLAUDECODE`が設定されている場合は、この一覧のファイル名を本セッションの処理対象として固定する。
   起動時の目的文にCodexオーケストレーターの連続処理と明記されている場合（以下、連続処理モード）は、
   後述の`process-loop`用再取得も適用する
2. 必要なファイル名だけを1回の`atk mq show <filename>... --target-repo=<repo-path> --skip-pull`で確認する
3. `plan_file`を持つフィードバックを計画実装型、それ以外を通常型とする。本文から型を推測しない
4. 本文の順序条件は着手可否の判定前に抽出し、active項目から対象ファイル名自身を除外した項目を依存先候補とする。候補を追加した依存グラフに自己依存又は循環が無いことを登録前に検査し、該当時は登録せず順序条件をTBDへ送る。検査を通過した候補だけを`atk mq set-dependencies <filename> --depends-on=<filename> ... --target-repo=<repo-path>`へ登録する。`--depends-on`を付けない実行は依存の全解除となるため使用しない。保存結果を照合する
5. `depends_on`が全て終端し、TBDは回答済みで、frontmatterと計画ファイルが有効な項目をreadyとする
6. readyな回答済みTBDの開始順と後始末は`references/hold-with-tbd-inject.md`に従う
7. readyなinbox項目を`atk mq start-processing`でprocessingへ移し、対象ファイル名と配置を照合する。
   既存のprocessing項目では`start-processing`を再実行せず、同コマンドの再実行を未完了の`feedbacks-planner`工程の再開起点にしない

`start-processing`が状態競合で拒否した場合は、active一覧と保存本文を再取得して着手可否の判定から再開する。

欠落依存、自己依存、循環、不正な`cooldown_until`、frontmatter破損、計画ファイル消失は修復対象とする。
過去の`queue_schedule.dependency`は読取互換だけ維持し、新規記録へ用いない。
計画実装型を1件以上扱う場合は`references/plan-impl-feedback-flow.md`を全文読む。

## 2. 調査と採否

複数項目を連続処理する場合、新しい項目の調査は前項目の調査結果・仮説・識別子を引き継がず
独立に確定する（努力目標）。

通常型の採否確定では、全項目の原文正本ID、人間由来の指示又は方針の優先度、調査根拠、欠陥原因、採否理由及び投入元を
項目ごとに対応付ける。`source: session-review`だけをエージェント由来と判定し、それ以外のsource、source欠落及び不明の不採用候補は
原文との差異と技術的理由を示す不採用確認用`user_decisions`へ送る。`user_decisions`は通常の将来判断TBDと区別する。
回答が得られた場合は、停止済みの識別子へ継続せず、出所と引用範囲を付けた逐語文を渡して
同じ`feedbacks-planner`系列（同じバッチと計画）の新しい識別子を起動し、当該項目の採否を確定する。
回答が得られない場合は同じ質問内容を不採用確認用TBDへ保存して保留し、回答を得られずTBDを確認できない状態ではrejectしない。
保留確認後は、停止済みの識別子へ継続せず、保留結果を渡して同じ系列の新しい`feedbacks-planner`識別子を起動する。
新規起動へ元のバッチ全項目の調査結果全文、原文frontmatterの`source`原値（欠落は値なし）、`user_decisions`の原文を渡す。
逐語回答又は保存TBD、同じ計画ファイルの絶対パスも渡す。保留項目を含む全項目の採否一覧と採用範囲だけで計画起草を続行する。
部分採用は確認経路へ機械的に含めず、差異、採用範囲、除外範囲及び理由を採否記録へ残す。起草担当又は実行主体へはバッチ全項目を渡し、
実施内容へは採用又は部分採用の採用範囲だけを反映する。
別リポジトリ項目は、投入前処理で入力メッセージの予約frontmatterキー`target_repo`だけを移管先の値へ一時的に置き換え、
元項目のfrontmatterと本文を含むメッセージ全体を正しい`target_repo`へ`agent-toolkit:add-feedback`で登録する。
通常の`atk mq add`はfrontmatterの`target_repo`をCLI値で置き換えず、frontmatterの値を優先する。
sourceがある場合は同じ値を渡す。
`alert_keys`などの非予約frontmatterは元項目の値を保持する。
移管先では`atk mq show`でsource（指定時）、本文、`target_repo`及び元項目の非予約frontmatter全体を照合する。
照合後に元項目を終端する。

Claude Codeホストでは、`feedbacks-planner`の起動前に`agent-toolkit:delegation`をSkill機能で起動する。
Claude Codeホストでは`references/feedbacks-planner-reception.md`を全文読み、active一覧を取得した時点のreadyな通常型項目を
1バッチとして1つの`agent-toolkit:feedbacks-planner`へ渡す。
`feedbacks-planner`への起動入力は`references/feedbacks-planner-reception.md`の列挙を正本とし、本文を起動文へ複製しない。
`feedbacks-planner`は各調査担当と起草担当へ同じ入力を渡し、各受信主体がファイル名ごとに
`atk mq show <filename> --target-repo=<repo> --skip-pull`を1回実行して本文を取得する。
調査と計画工程は対象worktreeを読み取り専用で共有し、項目別worktreeを作成しない。
readyな計画実装型のレーンは通常型バッチの計画工程を待たず、利用可能な書込担当枠で実装できる。

サブエージェント機能を利用できないCodexホストでは、通常型を次の順で扱う。
この経路では`references/explore-template.md`へ対象のフィードバックファイル名と対象リポジトリを渡す。

1. `references/review-checklists.md`を全文読む
2. 原文、現行実装、関連規範、履歴、既存の成功経路を調査する
3. バグ・障害・回帰では実行主体が`agent-toolkit:bugfix`をSkill機能で起動する
4. 横断調査を委譲する前に`agent-toolkit:delegation`をSkill機能で起動する。
   起動後は`references/explore-template.md`だけを受信者用のタスク文書として渡す
5. 単一要求は採用または不採用、独立した複数要求は採用、部分採用、不採用で判定する。
   原文の単一要求を薄めて部分採用しない
6. `references/decision-format.md`に従って採否と根拠を記録する

Claude Codeホストの通常型で`feedbacks-planner`から`status: awaiting_confirmation`と不採用確認用`user_decisions`を受領した場合は、確認待ち経路へ進む。
`status`を失敗処理より先に確認し、`awaiting_confirmation`を失敗又は`needs_escalation`として扱わない。
`source: session-review`と確認できる項目だけを利用者確認から除外する。その他のsource、source欠落及び不明の項目ごとに
原文との差異と技術的理由を示す`AskUserQuestion`を発行し、回答を得た場合は逐語文を渡して同じ系列の新しい`feedbacks-planner`識別子を起動し、
採否記録を再検収する。回答なしでは`references/hold-with-tbd-inject.md`に従い不採用確認用TBDを保存し、依存設定と`blocked`確認後の
保留結果を渡して同じ系列の新しい識別子を起動する。新規起動には元の全調査結果、原文frontmatterの`source`原値、`user_decisions`原文、
逐語回答又は保存TBD及び同じ計画ファイルの絶対パスを含める。保留項目を含む全項目の採否一覧と採用範囲だけで計画起草を続行し、元項目を保留する。

外部ツール、ライブラリ、サービスの挙動を成果物へ転記する前に、一次資料または実装で裏付ける。
技術的に確定できない事項とユーザー判断は保留へ送る。

`status: awaiting_confirmation`は上記の確認待ち経路で処理し、失敗TBDを作成しない。
`feedbacks-planner`の失敗又は解消不能な`needs_escalation`では、対象の元のファイル名ごとに失敗TBDを`agent-toolkit:add-feedback`で保存する。
失敗TBDには失敗した事象、期待値、実際値、発生条件を含める。
直接的原因、再開に必要な情報、元のファイル名も含める。
失敗TBDの保存コマンドの完了表示にエラーが無いことを確認する。
警告が出た場合は`atk mq show <失敗TBD filename> --target-repo=<repo>`で保存内容に欠落が無いことを確認する。
`source: session-review`と確認できる項目は、確認後に`atk mq reject <filename> --note=<失敗TBD filename>`で元のフィードバックを終端する。それ以外の項目は、`references/hold-with-tbd-inject.md`の「技術的失敗」に従い、失敗TBDを依存へ追加して`blocked`まで確認する。元のフィードバックをrejectせず、失敗TBDの回答後は不採用確認を再開せず、次の`process-feedbacks`セッションで新しい`feedbacks-planner`を起動して通常経路で元のフィードバックを再開する。
失敗TBDを保存できない場合と欠落を修復できない場合はrejectを実行せず、元のフィードバックをactiveのまま保持して失敗として返す。
`source: session-review`と確認できる項目でrejectだけが失敗した場合は、一意な失敗TBDとactiveな元のフィードバックを確認できる場合だけrejectを1回再実行する。
3分類、元のフィードバックの`feedbacks-planner`再開、Git状態の回復は行わない。

`feedbacks-planner`完了後の項目別結果はファイル名昇順で各1回反映する。
結果反映コマンドが警告・エラーを返した場合は、同じコマンドを再実行せず、
`atk mq show <filename> --target-repo=<repo>`で当該項目だけを1回再取得する。
意図した保存後状態なら重複操作を避ける。元のフィードバックがactiveなら前段と同じ失敗TBDの保存、確認及び由来に応じた終端処置を各1回実行する。`source: session-review`と確認できる項目はrejectで終端し、それ以外の項目は`references/hold-with-tbd-inject.md`の「技術的失敗」に従ってTBD依存を設定し、`blocked`を確認して保留する。後者では不採用確認を経ずに元のフィードバックをrejectしない。
再取得失敗、想定外状態、失敗TBDの保存失敗、reject再失敗では、当該項目への追加操作だけを止める。
全ての分岐で保持済みの`feedbacks-planner`結果により後続項目を各1回処理し、全件走査後に警告・エラーが1件でもあればバッチを失敗として返す。

回答済みTBDの処理は`references/hold-with-tbd-inject.md`を正本とする。

## 3. 保留

保留、解除条件、再開情報、TBD回答の扱いは`references/hold-with-tbd-inject.md`を正本とする。

readyな採用項目が無ければ、保留状態を維持して「6. 振り返りと終了」へ進む。
`process-loop`がactive状態の変化を検出し、着手可否の成立後に新しいセッションを起動する。

## 4. 実装と公開

本文を実装要求と、commit作成以降の終端工程（push特別指定、PR/MR、リリース、タグ付け、配布及び公開）へ分離する。
終端工程はレーン又は統合担当へ委譲しない。
終端工程を持つ項目は、公開する変更集合、公開先及びPR/MR操作集合を本文の明示記載から一意に確定し、不明な要素はTBDへ送る。
順序条件とPR/MR指定を併せ持つ項目を検出した直後、公開グループの操作前に`references/publish-group.md`を全文読む。
実装commitをpushする全経路でpush済みOIDのCI通過を確認し、レーンのcommitを未公開のままadoptしない。
PR/MRの作成、マージ又は作成＋マージ、リリースは、全レーンの統合、push及びCI通過の後、adoptの前にメインが1回だけ実行する。
本文に明記されない不可逆操作はTBDへ送る。
失敗時はpush済み内容を巻き戻さず、`references/hold-with-tbd-inject.md`に従って保留する。
終端工程だけを求める項目は計画を作成せず、終端待機集合へ登録して全レーンの統合とpush後にファイル名昇順で実行する。

- Claude Codeホストの通常型採用項目は、`feedbacks-planner`の統合計画を各フィードバックへ`atk mq convert-to-plan`で記録し、
  `references/plan-impl-feedback-flow.md`の計画実装型経路へ移行する
- Codexホストの通常型採用項目は実行主体が`agent-toolkit:plan-mode`をSkill機能で起動し、調査済み事実と採否を渡す
- 計画実装型は`references/plan-impl-feedback-flow.md`に従い、計画ファイルを正本として実装する
- commit前に実行主体が`agent-toolkit:commit`をSkill機能で起動する
- 実装と二系統レビューの完了後、呼び出し元がpushとCI通過確認を完遂する
- 計画の完了条件を満たした対象だけを後始末へ進める

メインはキュー操作、`feedbacks-planner`・`plan-impl-executor`・統合スレッドの起動と検収、TBDと新規フィードバックの投入、
上流取得、統合worktreeの作成と回収、push、CI通過確認を担当する。
レーンのcommitの適用、競合解消、履歴一本化、検証は統合担当へ委譲する。
統合担当のモデル解決と起動は`references/plan-impl-feedback-flow.md`を正本とする。

作業中に独立した新規改善を発見した場合は、実行主体が`agent-toolkit:add-feedback`をSkill機能で起動し、
完成済み本文と対象リポジトリを渡す。投入コマンドを本スキルから直接構成しない。

## 5. 後始末

- 不採用: 判定確定後に`atk mq reject <filename> --note=<理由>`を実行する
- 採用: 終端工程を持つ項目は全終端工程の成功後、持たない項目は対象commitのpushとCI通過確認後に
  `atk mq adopt <filename> --note=<反映概要> --commit=<完全長SHA>`を実行する
- 回答済みTBD: `references/hold-with-tbd-inject.md`に従って採用終端し、依存解除後の処理を再開する

各コマンドの保存結果を再取得し、対象、採否、note、commitを照合する。
終端工程を持つ項目のnoteには実施した操作と結果を記録する。

Codexホストと連続処理モードでは、後始末の完了後は再取得したready項目の有無で分岐し、ready項目があれば「2. 調査と採否」へ戻り、
ready項目が無い場合だけ「6. 振り返りと終了」へ進む。Codexホストと連続処理モードでは、再取得の範囲だけが異なる。
Claude Codeホストでは、ready項目を再取得せず、起動時に固定した未終端項目だけを処理し、後始末の完了後は常に「6. 振り返りと終了」へ進む。
更新された規範は次セッションの起動時に読み込む。起動後に追加された項目はactiveのまま残し、残る項目を次セッションで再集約して
並列調査・統合計画化できるため、時間・コストを抑える。
`process-loop`又は次回の手動起動による新しいセッションで扱う。
Codexでは実装と後始末の間にactive一覧を再取得し、追加分を含むready項目も対象とする。
連続処理モードでは、
取得済みのready項目を終端させたか保留した後にactive一覧を再取得し、
依存関係の有無を問わず追加分を含むready項目を対象とする。
「6. 振り返りと終了」の開始後に追加された項目は次回の手動起動で扱う。
既に本セッションのcommitで要求が満たされている場合は、その実測を根拠に採用として終端させる。

## 6. 振り返りと終了

全ready項目の処理又は保留永続化後に、実行主体が`agent-toolkit:session-review`をSkill機能で起動する。
振り返りで生成した提案は同スキルが`agent-toolkit:add-feedback`を起動して投入する。完了後に実行主体が
`agent-toolkit:exit-session`をSkill機能で起動する。

`exit-session`が到達できる終了の範囲は実行ホストによって異なる。
本体プロセスの停止を要求できないホストでは、終了理由を最終応答としてターンを完了させた時点で
終了工程が正常に完了したものとして扱う。プロセスの停止と次セッションの起動はホスト側の責務とする。

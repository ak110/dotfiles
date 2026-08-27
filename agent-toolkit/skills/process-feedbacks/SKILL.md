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
新規フィードバックとTBDの投入は`../add-feedback/SKILL.md`を正本とし、本スキルへ投入手順を複製しない。

本スキルの起動中は自律モードとする。ユーザー判断が必要な事項は
`agent-toolkit/rules/01-agent.md`「協調と自律」節に従って`AskUserQuestion`で確認し、
同節が定める契機（回答期限の超過、又は期限を提供しない実行環境では応答を得られないと判断した時点）で
TBDへ永続化して暫定判断で進める。

各工程の判断へ入る前に、対象に応じて次の資料を全文読む。

必要な工程へ入る時だけ対応資料を全文読む。

- 一括取得時は`agent-toolkit/skills/add-feedback/references/managed-temp-bulk-show.md`を全文読む
- 採否分類時は`agent-toolkit/skills/process-feedbacks/references/review-checklists.md`を全文読む
- 採否記録時は`agent-toolkit/skills/process-feedbacks/references/decision-format.md`を全文読む
- 採否分類で設計判断を行う時は`agent-toolkit/skills/coding-standards/references/design-heuristics.md`を全文読む
- 別リポジトリへ投入する時は`agent-toolkit/skills/add-feedback/references/cross-repository-submission.md`を全文読む
- TBDを保留する時は`agent-toolkit/skills/process-feedbacks/references/hold-with-tbd-inject.md`を全文読む
- 計画起草時は`agent-toolkit/skills/plan-mode/references/plan-file-standards.md`を全文読む
- レーン割当時は`agent-toolkit/skills/plan-mode/references/plan-impl-caller-reception.md`を全文読む
- 計画実装の担当を扱う時は`agent-toolkit/skills/plan-mode/references/implementation-task.md`を全文読む
- レーン割当時は`agent-toolkit/skills/delegation/references/runtime-routing.md`を全文読む
- Claude Code固有の委譲経路を扱う時は`agent-toolkit/skills/delegation/references/claude-code-runtime.md`を全文読む
- 待機時は`agent-toolkit/skills/delegation/references/waiting-and-monitoring.md`を全文読む
- レビュー継続時は`agent-toolkit/skills/plan-mode/references/review-loop-coordination.md`を全文読む
- 計画実装型を扱う時は`agent-toolkit/skills/process-feedbacks/references/plan-impl-feedback-flow.md`を全文読む
- 版数更新時は`.claude/skills/agent-toolkit-edit/references/version-bump.md`を全文読む

## 1. 入力と着手可否

対象リポジトリの絶対パスを確定し、次を行う。

1. `atk mq list --status=active --target-repo=<repo-path>`でactive項目を取得する。
   `CLAUDECODE`が設定されている場合は、この一覧のファイル名を本セッションの処理対象として固定する。
   起動時の目的文にCodexオーケストレーターの連続処理と明記されている場合（以下、連続処理モード）は、
   後述の`process-loop`用再取得も適用する
2. 必要なファイル名だけを、後述の「一括取得の管理対象一時領域」の手順で1回取得する
3. `plan_file`を持つフィードバックを計画実装型、それ以外を通常型とする。本文から型を推測しない
4. 本文の順序条件は着手可否の判定前に抽出する。active項目から対象ファイル名自身を除外し、`../add-feedback/SKILL.md`「入力」が定める外部待ち条件を依存先候補へ写像する。実装順序の前後は依存先候補へ含めない。
   別リポジトリの先行変更の完了待ちでは、依存先候補を手順1で対象リポジトリへ限定したactive一覧だけから選ばない。
   対象リポジトリを跨ぐ候補は、`atk mq show <filename> --target-repo=<referenced-repo> --skip-pull`で個別に存在を確認し、候補集合へ加える。
   日付境界を持つ条件では、境界日以前の項目をグループ終端項目の依存先とし、境界日後の項目を終端項目へ依存させる。
   導出集合を追加した依存グラフに自己依存や循環が無いことを登録前に検査する。該当時は登録せず順序条件をTBDへ送る。
   終端項目のadoptはPR/MRマージ後とする既存契約を維持する。日付帰属、終端項目もしくは公開先のマージ条件を一意に確定できない場合は、境界日後の項目を処理せずTBDへ送る。
   外部状態の解除時刻と観測経路が確定しない待機条件は、依存先候補から除外して保留理由を本文へ記録する。短時間のcooldownは解除時刻と観測経路がある候補として扱い、機械的に除外しない。
   検査を通過した各項目の`depends_on`を本文から導出した集合へ一致させる。集合が空でない場合は各ファイル名を`--depends-on`へ指定して`atk mq set-dependencies <filename> --depends-on=<filename> ... --target-repo=<repo-path>`を実行する。集合が空の場合は`--depends-on`を付けずに実行して依存を全解除する。保存結果を照合する
5. 手順4の一致操作を完了した後に、`depends_on`が全て終端し、TBDは回答済みで、frontmatterと計画ファイルが有効な項目をreadyとする。バッチの候補集合はready判定へ持ち込まない
6. readyな回答済みTBDの開始順と後始末は`references/hold-with-tbd-inject.md`に従う
7. 手順4で実装順序だけの依存を除去した同一バッチ内の項目も、同じ対象リポジトリのreadyなinbox項目としてファイル名昇順でまとめ、
   `atk mq start-processing <filename>... --target-repo=<repo-path>`を1回実行してprocessingへ移し、対象集合と配置を照合する。
   コマンドは全ファイルの存在、inbox配置、frontmatter及び`target_repo`一致を移動前に検証するため、1件でも失敗した場合は集合全体を拒否し、どの項目も移動しない。
   実装順序の保証は統合計画の実装単位順へ移し、`start-processing`へ新たな順序又は依存の検査を追加しない。
   既存のprocessing項目では`start-processing`を再実行せず、同コマンドの再実行を未完了の`feedbacks-planner`工程の再開起点にしない

feedbackのactive集合は`inbox`・`planning`・`processing`である。`planning`は計画作成中の通常型フィードバックとして一覧・詳細・serveで確認できるが、ready判定と`process-loop`の起動集合には含めない。TBD修復と`start-processing`の対象にも含めない。planning項目の計画作成、再開及び失敗復旧は、同じファイル名集合を指定する`plan-and-add-feedback`の経路だけで行う。

`start-processing`が状態競合で拒否した場合は、active一覧と保存本文を再取得して着手可否の判定から再開する。
移動開始後にI/O、commit又はpushが失敗した場合の管理リポジトリ復旧手順は`references/feedbacks-planner-reception.md`
「## 起動」の該当段落を正本とする。

欠落依存、自己依存、循環、不正な`cooldown_until`、frontmatter破損、計画ファイル消失は修復対象とする。
過去の`queue_schedule.dependency`は読取互換だけ維持し、新規記録へ用いない。
計画実装型を1件以上扱う場合は`references/plan-impl-feedback-flow.md`を全文読む。

### 一括取得の管理対象一時領域

同一対象リポジトリの複数ファイル名を同一工程で取得する時点で、`../add-feedback/references/managed-temp-bulk-show.md`を読み、同文書の手順を完了させる。

## 2. 調査と採否

各フィードバックの採否を判定する工程の開始時点で`references/decision-format.md`を全文読み、採否記録と`user_decisions`の累積レコード契約を適用する。

複数項目を連続処理する場合、新しい項目の調査は前項目の調査結果・仮説・識別子を引き継がず
独立に確定する（努力目標）。

通常型の採否確定では、全項目の原文正本ID、人間由来の指示又は方針の優先度、調査根拠、欠陥原因、採否理由及び投入元を
項目ごとに対応付ける。由来区分は`decision-format.md`「採否結果」の値集合を参照して判定し、エージェント由来でない不採用候補は
原文との差異と技術的理由を示す不採用確認用`user_decisions`へ送る。`user_decisions`は通常の将来判断TBDと区別する。
回答が得られた場合は、停止済みの識別子へ継続せず、出所と引用範囲を付けた逐語文を渡して
同じ`feedbacks-planner`系列（同じバッチと計画）の新しい識別子を起動し、当該項目の採否を確定する。
回答が得られない場合は同じ質問内容を不採用確認用TBDへ保存して保留し、回答を得られずTBDを確認できない状態ではrejectしない。
保留確認後は、停止済みの識別子へ継続せず、保留結果を渡して同じ系列の新しい`feedbacks-planner`識別子を起動する。
確認待ちを複数サイクルで処理する場合は、`references/decision-format.md`が定める原文正本IDごとの累積レコードを渡す。
初回起動には再開コンテキストを渡さない。
`awaiting_confirmation`後の再開起動だけは、元のバッチ全項目の調査結果全文、原文frontmatterの`source`原値（欠落は値なし）、IDごとの累積`user_decisions`、
出所と引用範囲付きの逐語回答・保存TBD、初回起動と同じ計画ファイルの絶対パスを全て渡す。
`feedbacks-planner`はバッチ全項目の採否記録を保持したまま、全要求不採用の項目をreject対象、保留項目をhold対象と計画スレッドの起動前に判定して計画対象集合から除外する。判定結果は完了報告でメインへ返し、キュー状態を変更しない。
部分採用は確認経路へ機械的に含めず、`references/decision-format.md`の採否記録へ残す。事前除外後の計画対象集合だけを計画担当へ渡す。
実装順序の保証は統合計画の計画ファイル（詳細）`### 実装単位`の先行依存と統合順へ移し、`start-processing`へ新たな順序・依存検査を追加しない。
別リポジトリ項目の投入と照合は`../add-feedback/references/cross-repository-submission.md`を正本とする。

Claude CodeとCodexの双方で、`feedbacks-planner`の起動前に`agent-toolkit:delegation`をSkill機能で起動する。
双方で`references/feedbacks-planner-reception.md`を全文読み、active一覧を取得した時点のreadyな通常型項目を
1バッチとして1つの`agent-toolkit:feedbacks-planner`へ渡す。
`feedbacks-planner`への起動入力は`references/feedbacks-planner-reception.md`の列挙を正本とし、キュー経路では本文を起動文へ複製しない。
キューにない素材の逐語本文・回答全文は計画外の明示入力として、調査、起草、初回レビュー、再レビューへ同じ値を保持する。
`feedbacks-planner`は各調査担当へ事前割当した素材IDとファイル名を渡す。
各調査担当は、担当が2件以上の場合は一括取得の管理対象一時領域の手順で取得する。
一括取得では`atk mq show <filename>... --target-repo=<repo> --skip-pull`を1回実行する。
担当が1件の場合は`atk mq show <filename> --target-repo=<repo> --skip-pull`で単数取得する。
警告・エラー後の当該項目だけの再取得も単数形とする。
計画担当は構造化入力にフィードバック由来素材があるときだけ、同じ対象リポジトリの全キューIDを
「一括取得の管理対象一時領域」の手順で対象リポジトリごとに1回取得する。警告・エラー後の当該項目だけの再取得は単数形を維持する。
計画担当は、取得した本文と内部採否記録を照合し、各フィードバックを`## 実施内容`の1行へ採否、採用範囲、実施しない範囲及び理由を欠落なく投影する。
フィードバック由来素材が無い場合、計画担当は取得を省略して出所と引用範囲を保持する。
調査と計画工程は対象worktreeを読み取り専用で共有し、項目別worktreeを作成しない。
readyな計画実装型のレーンは通常型バッチの計画工程を待たず、利用可能な実装担当枠で実装できる。

agent定義の欠落、frontmatterの写像不能又は`feedbacks-planner`の起動失敗は、メイン主体の別経路へ迂回せず失敗として返す。

Claude CodeとCodexのいずれかのホストの通常型で`feedbacks-planner`から`status: awaiting_confirmation`と不採用確認用`user_decisions`を受領した場合は、確認待ち経路へ進む。
`status`を失敗処理より先に確認し、`awaiting_confirmation`を失敗や`needs_escalation`として扱わない。
`decision-format.md`「採否結果」の値集合でエージェント由来と判定される項目だけをユーザー確認から除外する。それ以外の項目ごとに
原文との差異と技術的理由を示す`AskUserQuestion`を発行し、回答を得た場合は逐語文を渡して同じ系列の新しい`feedbacks-planner`識別子を起動し、
採否記録を再検収する。
回答なしの不採用確認用TBD、保存済みTBDの再開及び`blocked`状態の扱いは`references/hold-with-tbd-inject.md`を正本とする。

外部ツール、ライブラリ、サービスの挙動を成果物へ転記する前に、一次資料または実装で裏付ける。
技術的に確定できない事項とユーザー判断は保留へ送る。

`feedbacks-planner`の失敗時におけるTBD保存、全source共通の保留及び再開は`references/hold-with-tbd-inject.md`を正本とする。失敗TBDの必須項目と保存後の確認は`references/feedbacks-planner-reception.md`「受領」を適用する。

同一バッチかつ同一`target_repo`で、失敗した事象、期待値、実際値、発生条件、直接的原因及び再開に必要な情報が全て一致する失敗は、元のファイル名一覧を本文へ列挙した1件の共通失敗TBDへ集約する。失敗した元項目は投入元の由来にかかわらず同じTBDへ依存させ、`blocked`を確認する。技術的失敗を不採用へ変換せず、`atk mq reject`はprocess-loop内で要求の全てを不採用と確定した終端に限る。6要素又は`target_repo`が異なる場合は同じTBDへ集約しない。

`feedbacks-planner`完了後の項目別結果はファイル名昇順で各1回反映する。
保存済みの不採用確認用TBDを受領して再開した項目は、既存TBDの保存内容と元項目の`blocked`状態を確認済みであるため、結果反映時の失敗処理対象から除外する。
この項目では失敗TBDの再投入、`atk mq set-dependencies`による再依存、`atk mq return-to-inbox`による再inboxとrejectを実行せず、保持済みの結果を反映して次の項目へ進む。
結果反映コマンドが警告・エラーを返した場合は、同じコマンドを再実行せず、
`atk mq show <filename> --target-repo=<repo>`で当該項目だけを1回再取得する。
意図した保存後状態なら重複操作を避ける。元のフィードバックがactiveなら前段と同じ失敗TBDの保存、確認及び依存設定を各1回実行する。由来にかかわらず`references/hold-with-tbd-inject.md`の「技術的失敗」に従ってTBD依存を設定し、`blocked`を確認してactiveのまま保留する。不採用確認を経ずに元のフィードバックをrejectせず、失敗処理からrejectを呼び出さない。
保存済みの不採用確認用TBDを受領して再開した項目で結果反映が失敗した場合は、保持済みの確認TBDを同じ依存として残し、新しい失敗TBDを作成しない。
再取得失敗、想定外状態又は失敗TBDの保存失敗では、当該項目への追加操作だけを止める。
全ての分岐で保持済みの`feedbacks-planner`結果により後続項目を各1回処理し、全件走査後に警告・エラーが1件でもあればバッチを失敗として返す。

回答済みTBDの処理は`references/hold-with-tbd-inject.md`を正本とする。

## 3. 保留

保留、解除条件、再開情報、TBD回答の扱いは`references/hold-with-tbd-inject.md`を正本とする。

readyな採用項目が無ければ、保留状態を維持して「6. 振り返りと終了」へ進む。
`process-loop`がactive状態の変化を検出し、着手可否の成立後に新しいセッションを起動する。

## 4. 実装と公開

本文を実装要求と、commit作成以降の終端工程（push特別指定、PR/MR、リリース、タグ付け、配布及び公開）へ分離する。
終端工程はレーン又はマージ担当へ委譲しない。
終端工程を持つ項目は、公開する変更集合、公開先及びPR/MR操作集合を本文の明示記載から一意に確定し、不明な要素はTBDへ送る。
順序条件とPR/MR指定を併せ持つ項目を検出した直後、公開グループの操作前に`references/publish-group.md`を全文読む。
実装commitをpushする全経路でpush済みOIDのCI通過を確認し、レーンのcommitを未公開のままadoptしない。
PR/MRの作成、マージ又は作成＋マージ、リリースは、全レーンの統合、push及びCI通過の後、adoptの前にメインが1回だけ実行する。
本文に明記されない不可逆操作はTBDへ送る。
失敗時はpush済み内容を巻き戻さず、`references/hold-with-tbd-inject.md`に従って保留する。
終端工程だけを求める項目は計画を作成せず、終端待機集合へ登録して全レーンの統合とpush後にファイル名昇順で実行する。

- Claude CodeとCodexの双方の通常型採用項目は、`feedbacks-planner`が返した対応表の担当計画ファイルを各フィードバックへ`atk mq convert-to-plan`で記録し、
  `references/plan-impl-feedback-flow.md`の計画実装型経路へ移行する
- 計画実装型は`references/plan-impl-feedback-flow.md`に従い、計画ファイルを正本として実装する
- commit前に実行主体が`agent-toolkit:commit`をSkill機能で起動する
- 実装と準拠系・盲検系のレビューの完了後、呼び出し元がpushとCI通過確認を完遂する
- 計画の完了条件を満たした対象だけを後始末へ進める

メインはキュー操作、`feedbacks-planner`・`plan-impl-executor`の起動、レーン起動前の計画読解と検証区分の指定を担当する。
チェックポイントの検収と`SendMessage`による再開指示、マージ許可の直列発行、統合ブランチの作成とff統合も担当する。
版数bump、統合後検証、TBDと新規フィードバックの投入、上流取得、push、CI通過確認も担当する。
レーンworktree内のrebase・競合解消・ff前進はマージ担当へ委譲する。
チェックポイントへの対応（続行・是正・詳細要求・順序調整）は、排他制御・公開認可・検収基準などの安全境界を
守る範囲でメインが状況に応じて自律決定し、介入の要否判断に固定の手順を課さない。
同一レーンのレビューラウンドが反復する場合、スコープ逸脱を受領した場合及びチェックポイントが長時間届かない
場合の検出目安、統括の詳細な手順とチェックポイント種別は`references/plan-impl-feedback-flow.md`を正本とする。

作業中に独立した新規改善を発見した場合は、実行主体が`agent-toolkit:add-feedback`をSkill機能で起動し、
完成済み本文と対象リポジトリを渡す。投入コマンドを本スキルから直接構成しない。

## 5. 後始末

- 採用要求を含む項目: 終端工程を持つ項目は全終端工程の成功後、持たない項目は対象commitのpushとCI通過確認後に
  `atk mq adopt <filename> --note=<反映概要> --commit=<完全長SHA>`を実行する
- 回答済みTBD: `references/hold-with-tbd-inject.md`に従って採用終端し、依存解除後の処理を再開する

各コマンドの保存結果を再取得し、対象、採否、note、commitを照合する。
複数の採用項目もファイル名昇順で1件ずつ終端し、複数のファイル名を1回の`atk mq adopt`へ渡さない。全要求不採用とprocess-loop内で確定した項目のrejectも、判定済みの対象ごとに1件ずつ実行する。
複数の項目を連続して終端する場合は、ファイル名昇順で最後の1件を除く`atk mq adopt`・全要求不採用と確定した項目への`atk mq reject`へ`--skip-push`を付け、最後の1件は付けずに実行して滞留commitをまとめてpushする。対象が1件だけの場合は`--skip-push`を付けない。
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

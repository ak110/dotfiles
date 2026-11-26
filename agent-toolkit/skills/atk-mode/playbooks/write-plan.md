# 計画の作成

目的: 担当が対象WIを計画に固定し、中断や担当の交代があってもユーザーの要求どおりの実装へ手戻りなく進めるようにする。
対象リポジトリを変更する作業で、計画ファイルの要否を決め、必要な場合は初版を作って検査し、実装へ引き渡すまでの手順書である。
方針: 計画ファイルは、1つの作業で扱う対象WIの一覧だけを持つ内部資料であり、ユーザーの承認を求めない。
実装、実行レビュー、中断後の再開の基準は対象WIの本文であり、WIの本文から実装を始められない入力は、計画を作る前にWIの本文を詳細化する。
計画の体裁は`atk run-script plan-check`の診断に従って直し、体裁の差異は工程を止める理由から外す。
変更から生じた付帯作業（テスト、直接影響範囲の追随、整理）は、関連する開発と同じ計画、WI、commitで完了する。
レビューは、実装の後の実行レビューだけで行う。

調査だけの依頼（成果物の変更、操作、許可のいずれも求めない依頼）は、計画もcommitも作らず、根拠を示した説明か推奨を返して終え、実装や公開へ広げない。
ファイル操作か`atk`を使えない環境では、計画の原稿だけを返し、作成、検査、保存、実装が未実施であることを書く。

## 条件と行動

| 条件 | 行動 |
| --- | --- |
| 対象リポジトリを変更する作業を始める（エージェント向け文書の編集を含む） | 本書「計画ファイルの作成要否」で要否を決める |
| 計画を作る | 本書「進め方」を上から実行する |
| 計画を作らない（小変更の省略） | 依頼を基準にし、本書「メインによる起動の実装と終了」へ進む |
| Codexで計画工程へ着手する | 本書「計画ファイルの起草開始条件」「Plan modeの扱い」を当てる |
| 計画を書く、進捗を記録する、中断から再開する | `agent-toolkit:atk-mode/references/plan-format.md`を全文読み、該当する規定を適用する |
| 保持、不変性、完全復元を契約か確認の選択肢へ書く、過去の契約へ戻すか撤去する変更を詳細化する | `agent-toolkit:atk-mode/references/plan-format.md`を全文読み、「保持・不変性・完全復元の契約」「復元・巻き戻し型の変更」に従う |
| 計画を保存する、計画の名前か付属ファイルの参照を決める | `agent-toolkit:atk-mode/references/plan-storage.md`を全文読み、「計画ファイルの保存と参照」に従う |
| 公開の使い方が変わるかを判定する | `agent-toolkit:atk-mode/playbooks/process-wi-publishing.md`「使い方が変わる変更」を読む |
| 保存済みの旧形式の計画を読む、読み取り互換の実装か検査を変える | `agent-toolkit:atk-mode/references/plan-legacy-formats.md`を読む |

## 計画ファイルの作成要否

リポジトリを変更する作業は、新しく始めるレーンと作業の全てで計画を作る（一覧だけのため費用はほぼ無い）。
実行レビュー指摘管理表と対応記録は、計画と同じstemに置く。

- WIの無い協調モードの依頼で計画を省けるのは、バグ対応以外で、既存の値か文字列の差し替えだけで完結し、保存する継続的な方針も見直す既存箇所も構造上生じない場合に限る（AWIも作らない）。
  省く時は`agent-toolkit:atk-mode`「ユーザーの方針」に従い、指定手段と比べて採用する別案か、別案が不要な理由を引き継ぎ記録へ1行で残す。
- 公開の時点で計画なしで進行中のレーン（選定結果の`再開位置`が`計画なし`を指す項目）だけは、計画なしの経路で再開から統合まで終える（別名の管理表と対応記録、`plan-check`の再開位置の除外は互換として残る）。
- 継続的な方針の保存（`agent-toolkit:atk-mode/references/awi-body.md`「継続的な方針の保存」）と採否の記録は、計画の有無によらず行う。

## 詳細化

計画の一覧に載せるWIのうち、次のものは計画を作る前に本文を詳細化する。

| 入力 | 扱い |
| --- | --- |
| `source`が`add-awi-by-user`・`process-user-comments`・`implement`のAWI、詳細化済みの印を持つAWI | 詳細化しない。本文をそのまま基準にする |
| それ以外のAWI | `atk wi edit <ファイル名> --body-file <原稿> --detail`で本文を置き換え、詳細化済みの印を付ける |
| 回答済みUWI | UWIは置き換えず一覧に残す。作業を伴う回答の扱いは表の後の「回答済みUWIの扱い」に従う |
| WIの無い協調モードの依頼 | add-awi-by-userと同じ粒度のAWIを`atk wi add --detailed --hold`で保存し、一覧に載せる。完了時に`atk wi adopt`で終える |

回答済みUWIの扱い:

- 元のAWIが同じレーンにあり`詳細化あり`なら、回答をそのAWIへ反映して詳細化する。
- 元のAWIが同じレーンにあり`詳細化なし`（対象外の`source`か印あり）なら、AWIの本文は変えず、UWIの`## 回答`をそのまま実装と実行レビューの基準にする。
  UWIは一覧に載るため実装担当が読み、実行レビューはUWIの`## 回答`を原文要求として判定する（`agent-toolkit:atk-mode/references/review-contract.md`「記録する行と要求単位」）。
- UWIが元のAWIを持たない時だけ、回答から`atk wi add --detailed --hold`で詳細化済みのAWIを作って一覧へ加える。
  原稿のfrontmatterの`proposal_id`は`answer/<UWIのファイル名>`とし、中断後の再投入では保存済みの1件を使う。
  加えたAWIは検査の集合へ含める（レーンでは`plan-check`の`--added-wi`、single-laneでは対応表へ加えて`--expected-wi`）。
- 元のAWIが終了済み・回答待ち・別の担当の処理中なら新しいAWIを作らず、`agent-toolkit:atk-mode/playbooks/process-wi-selection.md`の扱いに従う。
- 回答が独立した新しい作業を明示した時は、元のAWIの状態によらず`agent-toolkit:atk-mode/references/answer-transitions.md`手順2で独立したAWIを起草するかを判定する。
- 作業を伴わない回答（質問への回答だけ、提案を退ける回答）はAWIを加えない。
- 統合（single-laneでは終了処理）で、UWIは`agent-toolkit:atk-mode/references/answer-transitions.md`のとおり`adopt`か`reject`で、加えたAWIは`atk wi adopt`で終える。

- 詳細化は、add-awi-by-userの起草と同じ調査と判断（`agent-toolkit:atk-mode/playbooks/wi-drafting.md`「起票目的別の調査出口」の手動作成で具体化を求めた要求の水準）で行う。
  本文の書き方は`agent-toolkit:atk-mode/references/awi-body.md`に従う。
  元の要求と`## ユーザー指摘の逐語引用`は保ち、人間由来の要求を縮める時は`agent-toolkit:atk-mode/references/origin-and-approval.md`「承認」に従う。
  独立した要求は新しいAWIへ分けてinboxへ投入する。
  投入前レビューは課さず、詳細化した本文は実行レビューが基準として確かめる。
- `atk wi edit --detail`は、`processing_owner`と同じ会話から、印を持たず対象外の`source`でないAWIへ1回だけ受け付ける（委譲先のレーン担当は委譲元の会話に解決される）。
- 新しく保存するAWIの原稿にはfrontmatterの`proposal_id`を付ける（回答から作るAWIは`answer/<UWIのファイル名>`、協調モードの依頼は`implement/<会話の識別子>/<連番>`）。
  保存の後に中断しても、同じ原稿を再投入すると`atk wi add`が保存済みの1件を示すため、それを使う。
  保存したファイル名は引き継ぎ記録へ残す。
- 変更が`agent-basics:formal-verification`「当てるかの判定」の対象に触れる時は、同節で判定し、`形式検証の判定:`の行を、詳細化するWIでは`## 調査結果と判断根拠`へ、詳細化しないWIでは引き継ぎ記録へ行頭から書く。
  対象コードを検証する既存のスキルがあれば、その再実行と追従を変更範囲の検証へ含める。

## 確認と起動経路

確認の要否、手段、UWIへの退避は`agent-toolkit:atk-mode/playbooks/confirmation.md`に従い、入力のWIで確定済みの事項はそのまま使う。
協調モードのメインは、`agent-toolkit:atk-mode/references/choice-construction.md`「質問ラウンド」でユーザーと認識を合わせてから起草する。
`agent-toolkit:atk-mode`の作業`process-wi`のレーン担当は、確認事項が生じたら`agent-toolkit:atk-mode/playbooks/subagent.md`「確認事項の即時通知」で委譲元へ通知し、回答を得てから再開する（UWIの登録は委譲元が担う）。
レーンの起草と待機の区切りは`agent-toolkit:atk-mode/playbooks/process-wi-execution.md`が定める。

## 進め方

追加の発話かレビュー指摘を受けたら要求と現物を比べ、該当する手順へ戻って検査し直す。
観測で前提が成り立たなくなった時は、確かめた成果と方針を保って次の確認を選ぶ（`agent-basics:writing-standards/references/investigation.md`「仮説から次の確認を選ぶ」）。

1. 本書「計画ファイルの作成要否」で要否を決める。
   計画を作らない場合は、本書「条件と行動」の該当の行へ進む。
2. 本書「詳細化」で、一覧に載せるWIを確定し、詳細化が要るWIを詳細化する。
   - 作業種別がバグ対応で、WIの原因分析が無いか直す時は、詳細化の前に`agent-basics:bugfix`を起動して7つの問いに答え、WIの`## 原因分析`へ書く。
   - 採否を左右する技術的な不確実性は、詳細化を確定する前に試作などで解消する（基準は`agent-basics:writing-standards`）。
3. 変更対象と方針を左右する未確定の判断を整理し（WIで確定済みの事項を除く）、`agent-toolkit:atk-mode/playbooks/confirmation.md`「確認要否の判定」を当てる。
   実装中に生じた同様の判断も同じに扱う。
   方針を確定する前に`agent-basics:writing-standards/references/quality-principles-subtraction.md`「変更の範囲」の既存例と比べ、比較先と結論を詳細化するWIの`## 調査結果と判断根拠`（詳細化しないWIは引き継ぎ記録）へ書く。
4. `agent-toolkit:atk-mode/references/plan-format.md`「初回起草の雛形」へ対象WIを記入し、原稿をmanaged-tempへ保存する。
   `atk run-script plan-create -- --main-source <原稿の絶対パス> --name <名称> --work-dir <対象リポジトリ>`で計画ファイルを作る。
   レーン担当は`--name`の代わりに`--lane <レーン識別子>`を渡す（`agent-toolkit:atk-mode/playbooks/process-wi-execution.md`）。
5. 実装に着手する前に`plan-check`を単独で実行し（本書「計画構造の自動チェック」）、終了コード0と警告が無いことを確かめる。
   メインは`--agent-rule-path`と`--handoff-file`を、レーン担当はそれに加えて選定結果とレーン識別子を付ける。
6. `起動経路`に応じて、次のどちらか1つを行う。

| `起動経路` | 手順5の後に行うこと |
| --- | --- |
| メインによる起動 | 本書「メインによる起動の実装と終了」 |
| `agent-toolkit:atk-mode`の作業`process-wi`のレーン担当としての起動 | `agent-toolkit:atk-mode/playbooks/process-wi-execution.md`の待機条件と、条件に応じた報告・通知・実装の手順 |

実装に着手した後の本文の凍結と要件判断の記録は、`agent-toolkit:atk-mode/references/plan-format.md`「計画ファイルの構成」に従う。
中断からの再開は、引き継ぎ記録とcommit履歴から最後に完了した工程の次を選ぶ（`agent-toolkit:atk-mode/playbooks/process-wi-execution.md`「再開」）。

### 計画ファイルの起草開始条件

計画ファイルの初版は、ホストのPlan modeの有無によらず、関連する調査を終えて未確定の事項を解消した段階で起草する。
ユーザーの判断が必要な事項は`agent-toolkit:atk-mode/playbooks/confirmation.md`の手順で確認かUWIへの記録を済ませ、暫定の判断を終えた事項だけを計画に含める。
Codexの`update_plan`を使う場合は「調査」と「計画ファイル起草」を別のステップにし、調査を`completed`にする時に`explanation`へ確認対象、確認手段、確定事項、未確定事項の有無を書く（努力目標。起草を始めた時点の確認を後から追跡できるようにするため）。
起草の途中で事実の不足が分かったら、調査へ戻る。
レビュー指摘への対応と引き継ぎ記録の追記は、この条件の対象外とする。

### Plan modeの扱い

Plan modeの状態はターン開始の時点のホストの状態で判定し、切り替えはホストの操作でだけ起きるものとして扱う。
ネイティブのPlan modeが有効な場合は、ホストが提供する制限、Plan専用の対話、承認後の状態遷移を使い、ファイル変更が遮断される範囲はホストの公開仕様を根拠に書く。
ネイティブのPlan modeが有効でない場合は、計画ファイル以外のファイルを変更せず、開始と終了を宣言する互換の運用を当てる。
どちらの場合も、計画の作成に必要な読み取りは行える。
ネイティブのモードが保証する範囲と互換の運用の範囲は、区別して書く。

### 計画構造の自動チェック

検査の形は`atk run-script plan-check -- --reject-migration-warnings <計画ファイルの絶対パス>`である。
`--reject-migration-warnings`は新規作成と改訂で付け、保存済みの計画を読むだけの検査では省く（旧形式は移行の警告を出して受け付ける）。
診断が示す直し方で本文を直し、同じ引数で再実行する。
WIリスト形式の計画では、一覧のWI本文の詳細化済みの印、`--agent-rule-path`のパス、形式検証の判定の欠落も検査する（選定結果の有無によらない）。
検査が確かめるのは形式であり、根拠の妥当性と検討の中身はレビューで確かめる。

レーンの検査では次を守る（各オプションの意味は`atk run-script plan-check -- --help`が示す）。

- `--selection-file`と`--lane`を付けた検査は、`再開位置`を持たない未統合のWIの全件に`--added-wi`を加えた集合と`related_wis`の一致を求める。
  `再開位置`を持つWIは既存の計画を引き継ぐため、`related_wis`へ含めると余剰として拒否される。
- `--expected-wi`には保存済みの対応表のうち、その計画のworktreeに属する項目を全件渡し、マージ用AWIと別worktreeの項目は除く。
- 凍結した計画の後に同じレーンへAWIを足した時は、先行する計画を全て`--prior-plan`で渡す。
- 初回に計画を分けた時は、全ての計画を位置引数に並べて1回で検査する。
  1件ずつ他の計画を`--prior-plan`に指定すると、未検査の計画のWIが集合に入らず失敗する。

### 節名のかぎ括弧

固定の節名は、計画構造の定数と同じ名前で示す。
日本語と半角文字が隣接する節名をかぎ括弧で囲むと、JTF 3.1.1の自動修正が隣接の空白を削り、計画構造の自動チェックが節名の不一致を誤って検出する。

## メインによる起動の実装と終了

メインは、計画の起草から実装、実行レビューの指摘の反映、計画の保存までを自ら担い、専用のworktreeを作らずに、要求を受けた時点の作業ツリーで実装する。
記録は計画の有無によらず引き継ぎ記録へ書き、計画を作らない作業では手順4の計画の保存を省く。

1. 対象WIの`## 反映内容と反映先`と`## 完成条件`に沿って実装する（保存済みの旧形式の計画で再開する時は`agent-toolkit:atk-mode/references/plan-legacy-formats.md`の表で節を読み替える）。
   - 着手の前に、完成条件ごとに公開された呼び出し手段、入力と初期状態、期待結果、検出すべき失敗の様態を具体化する。
     `agent-basics:writing-standards/references/testing-design.md`「検証単位の選定」に従い、期待結果を観測できる手段と環境を選ぶ。
     不具合は該当する入力で再現させてから直し、レビュー指摘の修正でも同じにする。
   - Gitの管理対象外のファイルを変える場合は、最初の変更の前に複製を作り、元と複製の対応を引き継ぎ記録へ記録する。
   - 各完成条件の結果を自分で確かめ、`検証結果`を`agent-toolkit:atk-mode/playbooks/verification.md`「完成条件証拠の検証記録」の形で引き継ぎ記録へ記録し、自分が担う残りの工程の担当と再開先も書く。
   - 最後の実装commitの前に、差分を`agent-basics:writing-standards/references/design-heuristics.md`「設計品質の劣化確認」で評価し、劣化を直して観点ごとの結論を`検証結果`へ書く。
   - 引き継ぎ記録に書いた変更範囲の検証コマンドを、実際のGit差分と直接影響範囲を覆うことを確かめてから対象の作業ツリーで実行し、`agent-toolkit:atk-mode/playbooks/commit.md`でcommitを作る。
2. `agent-toolkit:atk-mode/roles/exec-review.parent.md`に従って実行レビューを起動し、結果を受領する。
   指摘は自ら直し、修正commitの履歴の統合は`agent-toolkit:atk-mode/playbooks/history-rewrite.md`「修正方法の選択」に従う。
3. 完了へ進む前に、実行レビューの未解決の指摘が0件であること（画面差分がある場合はユーザビリティレビューが`合格`か`未確定`であること）を確かめる。
   画面レビューの合格を受理する前に、`類例の比較:`に製品内の同種画面か探索の範囲と外見の判定があることも確かめる。
   レビュー返却の`気付いた改善点:`にある差分外の既存不良も、同じ手順書に沿って処置へ渡す。
   リポジトリ全体の自動チェックとCIの判定は、後続の公開工程で行う。
4. 完了判定を引き継ぎ記録へ記録し、`atk plans commit <計画ファイル名>`で計画バンドルを保存する。
   WIの無い依頼で保存したAWIは`atk wi adopt`で終える。
5. 合意した公開範囲で`agent-toolkit:atk-mode/playbooks/publish.md`に従って公開する。
   `agent-toolkit:atk-mode/playbooks/report-completion.md`で成果、検証、公開の結果をまとめた作業完了報告を届け、同書の終了処理まで行う。
   計画の保存か公開の成功を要約しただけで終えず、同期の振り返りは起動しない。

## 返す報告に含める項目

計画の要否、詳細化したWIと新しく保存したAWI、計画ファイルの絶対パス、`plan-create`と`plan-check`の終了コードと警告、確認へ回した事項、メインによる起動では実装、実行レビュー、計画の保存、公開の結果を返す。

# CI失敗の帰属と原因分析

CIの失敗について、原因箇所、セッションへの帰属、および再現性を実際の観測に基づいて分類し、根本原因に直接作用する対処を選択する。
push先のリモート、更新対象のref、runの監視、ならびに証拠の一時保存と後始末は、`agent-toolkit:commit`のpushおよびCIに関する規定に従う。

失敗ログから、自セッションの変更に起因する明白で単純な失敗（lint違反、フォーマッターの適用漏れ、期待値の単純な未追随など）であると確定できる場合は、「失敗の性質による分類」および「分類判定の補助」に定める競合仮説の評価を省略してよい。ただし、`agent-basics:bugfix/references/root-cause-analysis.md`「初動と拡張原因分析の判定」に基づく拡張原因分析の要否判定は省略しない（自セッションに起因するpush後のCI失敗は対象条件に該当する）。修正コミットが必要な場合の修正担当者は、「修正commitが必要なCI失敗を修正するエージェント」に従って決定する。

## 名指し参照の読込方法

名指し参照は`../SKILL.md`「名指し参照の読込方法」に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../commit/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/commit/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `root-cause-analysis.md` | 判断 | `read:agent-toolkit:*/skills/bugfix/references/root-cause-analysis.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:bugfix/references/root-cause-analysis.md#「初動と拡張原因分析の判定」` | 判断 | `section:agent-basics:*/skills/bugfix/references/root-cause-analysis.md#「初動と拡張原因分析の判定」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../commit/references/push-and-ci.md#「pushと監視」` | 判断 | `section:agent-toolkit:*/skills/commit/references/push-and-ci.md#「pushと監視」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:bugfix/references/ci-failure-handling.md` | 判断 | `read:agent-basics:*/skills/bugfix/references/ci-failure-handling.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../process-wi/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:bugfix/references/root-cause-analysis.md` | 判断 | `read:agent-basics:*/skills/bugfix/references/root-cause-analysis.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../../share/exec.parent.md` | 判断 | `read:agent-toolkit:*/share/exec.parent.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../review-standards/references/exec-review-recording.md#「受理済み判定への参照」` | 判断 | `section:agent-toolkit:*/skills/review-standards/references/exec-review-recording.md#「受理済み判定への参照」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:bugfix/references/ci-failure-handling.md#「失敗の性質による分類」` | 判断 | `section:agent-basics:*/skills/bugfix/references/ci-failure-handling.md#「失敗の性質による分類」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:bugfix/references/ci-failure-handling.md#「対処法の選択肢と判定基準」` | 判断 | `section:agent-basics:*/skills/bugfix/references/ci-failure-handling.md#「対処法の選択肢と判定基準」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:bugfix/references/ci-failure-handling.md#「分類判断のための詳細検討」` | 判断 | `section:agent-basics:*/skills/bugfix/references/ci-failure-handling.md#「分類判断のための詳細検討」` |

## 失敗ログとartifactの取得

- 監視処理から取得したrun ID（GitLabの場合はpipeline ID）とjob IDを引き継ぎ、全体の実行状態を確認したうえで失敗ジョブのログを取得する。`gh`および`glab`コマンドの受付形式は、実行直前のヘルプで確認する
  - GitHubにおいてrun全体の進行中に対象ジョブが完了している場合は、`gh api repos/<OWNER>/<REPO>/actions/jobs/<job ID>/logs --allow-escape-sequences`を実行し、対象ジョブの全ログをmanaged-tempへ暫定保存する。端末エスケープシーケンスを含む応答は、このオプションを指定しないと取得が拒否される。ログが取得できない場合やジョブが進行中の場合は、アノテーションや失敗したテスト名など取得可能な証拠を用いて先行分析を進める
  - run全体の終了後は、`gh run view <run ID> --repo <OWNER>/<REPO> --json jobs`を実行して失敗したすべてのジョブのIDを確認し、各ジョブの全ログを同一のjob logs APIで保存する。`gh run view --log-failed`は、64KiBを超える長い行（巨大なパラメーターを含むテスト名など）が存在すると、それ以降の出力を欠落させたまま終了コード0で終了することがあるため、失敗ログの取得や失敗の有無の判定には使用しない。失敗ジョブ全体の確認は、`wait_ci.py`を再呼び出しして全体の完了結果を受け取ってから行う。待機の再開にあたっては、終了コード6の場合は同一引数で再実行し、終了コード2の場合は`agent-toolkit:commit`の`references/push-and-ci.md`「pushと監視」の終了コード2に関する規定に従う（1は完了したCIの失敗、3は待機処理自体の失敗）。同規定に従い、同一のSHAおよびbaselineまたは待機専用記録を維持したまま、`--timeout`を延長して再呼び出しする
  - 観測結果と再検証手順は、`docs/development/audit-records.md`の「agent-toolkit/skills/bugfix/references/ci-failure-handling.md：GitHubの状態別ログ取得：2026年10月1日」に記載されている。`--log-failed`による出力欠落の観測記録は、同ファイルの「agent-toolkit/skills/bugfix/references/ci-failure-handling.md：GitHubの状態別ログ取得：2026年10月24日」にある
- VRTの差分画像やtest-resultsなどのartifactも併せて取得する。artifactが存在しない失敗については、ログに記録された失敗テスト名、ステップ名、およびエラーメッセージから分類する
- 同一のコミットSHAを用いて、ローカル環境での再現テストを試みる

## 品質の分類

失敗の性質による分類、対処の選択肢と判定基準、および分類判定の補助については、`agent-basics:bugfix`の`references/ci-failure-handling.md`を全文読み込んで適用する。

## 選択の流れ

非決定的な動作（フレーク）の疑いを検証するための対象ジョブの再実行は、計画モード（plan mode）の開始前に行う。

1. 失敗ジョブを検出した際は、計画モードの開始前に取得可能な証拠を暫定保存し、全体の完了を待たずにローカルでの再現確認、帰属の判定、および原因仮説の検討に着手する（待機時間を有効に分析へ充てるための努力目標）。`wait_ci.py`を再開する場合は同一のbaselineを用い、SHA待機の場合は同一の`--failure-record`を使用する。全体完了後に、失敗したすべてのジョブのログやartifactを先行分析の結果へ統合する
2. 初回ログの内容と、同一SHAを用いたローカル再現の結果から、再現性を暫定的に分類する
3. 失敗したジョブの全容を確認したうえで修正範囲を確定し、計画モードにおいて新しい計画ファイル（バグ）の調査工程として3つの観点（VRTの場合は差分の分類も含む）を記録する。`agent-toolkit:process-wi`の公開工程では、公開工程が管理する原因分析結果へ記録する
4. 自セッションに起因する失敗、または起因を否定できない失敗である場合は、自明な単純失敗を除き、初回ログの見え方にかかわらず競合仮説の支持・反証および判別実験を記録する。そのうえで、`agent-basics:bugfix/references/root-cause-analysis.md`「初動と拡張原因分析の判定」および`references/root-cause-analysis.md`に従って原因、類似箇所の見直し、および処置の階層を確定する
5. 環境要因、または自セッションの変更と無関係であると分類した場合は、`atk wi list --type=awi --status=active`で同一事象を扱うAWIを検索する。該当するAWIが存在する場合は、`atk wi show <filename> --skip-pull`で本文に記載された緩和策を確認し、前述の対処選択肢と同じ階層で比較検討する
6. 選択した対処、基準情報、CIの生の観測事実、および分析結果を引き継ぎ、計画ファイル（バグ）の初版を起草する。公開工程においては、公開担当がこれらの情報をCI記録として保持し、CI修正担当へ引き渡してメインの作業ツリーで修正させる

## 修正commitが必要なCI失敗を修正するエージェント

原因分析によって修正コミットが必要と確定したCI失敗は、本節が定めるエージェントが修正を担当する（元の計画の実行工程へそのまま再投入しない）。
本節を適用するエージェントが対象作業ツリーの書き込み所有権を保持しており、そのツリーに書き込み中の稼働エージェントが存在しない場合は、自ら修正する。それ以外の場合は、`CI修正担当`を1件起動して修正を委譲する（起動手順と入力形式は`${CLAUDE_PLUGIN_ROOT}/share/exec.parent.md`に従う）。

`CI記録`は、原因分析結果、修正認可根拠、対象の検証結果、変更目的、対象、公開契約、および入力計画の7項目で構成する。

- 修正認可根拠: 計画ファイルに記録された元の要求または確認回答の該当箇所、原因となった変更を認可したユーザー指示の原文、あるいは既存の公開契約の該当箇所を記載する。根拠を特定できない場合は修正担当を起動せず、ユーザーへ確認する
- 原因特定の目的で診断コードを追加する場合は、「原因分析結果」において競合する仮説ごとに検証すべき観測内容を対応付け、「公開契約」において採取する対象と属性を対応付ける。全件の列挙や走査は仮説の判別に不可欠な場合にのみ限定し、データが大きい場合は一時領域への保存と範囲読み取りを活用する（完全性を損なうような容量上限を新設することは避ける）。元の失敗と終了コードは保持し、情報採取自体の失敗は別途記録する
- レビュー基準には、原因コミット自身に対応する計画が存在する場合はその計画を採用し、存在しない場合はCI記録を採用する（処理対象WIに含まれる無関係な別の計画を使用しない）
- 計画を持たないCI修正コミットが再push後に別の失敗の原因となった場合は、直前のCI記録を引き継いで7項目を更新する。CI対応レビュー指摘管理表が存在する場合は、そのパスおよび`修正系列の開始時のHEAD`も併せて引き継ぐ

修正者は、原因の修正、全項目の検証、差分の検収、ステージング、およびコミットまでを完了する。修正前に起草する計画は「選択の流れ」の定めに従い、公開工程外で自ら修正するエージェントは手順6の計画に従う。

### 修正系列

計画を基準とする後続の実行レビューでは、計画の進捗ログに記録された読み取り可能な受理済み実行レビュー返却ファイルの絶対パスを、任意入力`受理済みの完成条件証拠`として渡す（存在しない場合は`なし`とする）。利用可能な条件は、`agent-toolkit:review-standards`の`references/exec-review-recording.md`「受理済み判定への参照」に従う。受領確認を終えた新たな返却ファイルの所在も同一の進捗ログへ記録し、修正系列における後続の起動から参照できるようにする。

最初のCI失敗（公開工程においては全体検証の失敗を含む）から、CIが成功するか工程が終了するまでの一連の過程を「修正系列」と呼ぶ。修正回数、CI記録、レビュー指摘管理表、および`修正系列の開始時のHEAD`はこの単位で引き継ぎ、原因コミットが変わった場合でも回数のカウントを継続する。修正回数は是正した失敗に対応する修正の件数として数え、原因コミットへ取り込んだ修正（`agent-toolkit:commit`の`references/push-and-ci.md`「pushと監視」の「原因commitへ取り込む区分」）も1回として計算する。コミットを取り込んだ場合、`exec-review`へ渡す`原因commitOID`には履歴書き換え後に解決可能な値を指定し、書き換え前後のOIDの対応関係と`git range-diff`の結果をCI記録へ記録する。

修正系列を完了とするCIの成功判定は、失敗したジョブが判定対象に含まれ、期待される終了状態（許容された失敗を含む）で正常終了したパイプライン（GitHub Actionsではrun）に限定される。push先のリモートで対象ジョブが実行されない場合（保護ブランチ限定、スケジュール実行、特定パスの変更条件など）は、ジョブの実行環境を含めたローカル再現（「分類判定の補助」）によって成否を確認する。ローカル再現も行えない場合は、未確認のジョブ、試みた再現手順、および残存する実行条件の差異をエスカレーションとして報告する。

`入力計画`には、原因コミットに対応する計画ファイルのパスを指定する。

- 保存済みの計画ファイルは、起動側エージェントが起動前に`atk plans checkout <private-notes/plans/からの相対パス>`を実行して`~/.claude/plans`へ取得し、そのパスを渡す。保存済みパスを直接渡すと、進捗ログや指摘管理表への記録が拒否される。同一の修正系列における実行レビュー（`レビュー基準: 計画`）へも同一のパスを渡す
- 未保存の計画ファイルの場合は、`~/.claude/plans`のパスをそのまま渡し、対応する計画が存在しない場合は`なし`とする
- 修正系列の完了時に、起動側エージェントが`atk plans commit <private-notes/plans/からの相対パス>`を実行して再保存し、成功メッセージと警告がないことを確認する。CI記録を基準とする`ci-<修正系列の開始時のHEAD>.exec-review.tsv`も同一のタイミングで`atk plans commit ci-<修正系列の開始時のHEAD>.exec-review.tsv`を実行して保存する。修正系列が継続している間（再pushの結果待ちを含む）は表を`~/.claude/plans`に残し、先行して保存した場合は`atk plans checkout ci/ci-<修正系列の開始時のHEAD>.exec-review.tsv`で復元してから作業を継続する

同一の修正系列において3回目以降となる修正と、回数にかかわらず公開契約や設計に及ぶ修正では、単一の`exec-review`を合格するまで反復して実施する（公開工程の修正系列にも同様に適用する）。それ以外の場合は`exec-review`を起動せず、再pushを行ってCI確認へ戻る。2回目までの修正を修正者自身の確認で完了させ、3回目以降でレビューを起動するのは、3回以上の修正が必要になる事態が直前の修正で根本原因を捉え切れていないことを示すためである。CI対応レビュー指摘管理表の作成、更新、および保存は、`exec-review`を起動する場合にのみ行う。
`exec-review`を起動しない修正では、失敗ログの該当箇所を要約せずに精読し、失敗した検証処理が参照している実装コードや期待値の現物と比較して直接的原因を特定する。修正後は対象の検証処理を同一条件で再実行して期待通りの終了状態を確認し、影響を受ける可能性のある他のチェック処理も再実行する。
外部サービスの障害など、コード修正コミットを必要としない失敗については実装担当を起動しない。

### 公開工程の公開担当が扱うCI修正

`agent-toolkit:process-wi`の公開工程におけるCI修正は公開工程の内部で完結させ、新たな計画ファイル（バグ）は作成しない。
公開担当は7項目のCI記録を保持し、メインの作業ツリーを対象とする`CI修正担当`へ修正させる（本節冒頭で定めた自ら修正する原則に対する例外であり、具体的な手順は`${CLAUDE_PLUGIN_ROOT}/share/session-termination.subagent.md`に従う。所在参照）。
全体検証の失敗も同一の修正系列としてカウントし、`exec-review`の起動条件および起動しない修正の前提条件は前述の「修正系列」と同一とする。
修正コミットを作成するたびにバージョン番号とマニフェストを再判定したうえで再pushを行い、CI確認へ戻る。再push後に新たな失敗が発生した場合は、新しい原因コミットについて対応する計画ファイルの有無を再判定する。

## 失敗の性質による分類

品質基準の詳細は、`agent-basics:bugfix`の`references/ci-failure-handling.md`「失敗の性質による分類」を参照して適用する。

## 対処選択肢と判定基準

品質基準の詳細は、`agent-basics:bugfix`の`references/ci-failure-handling.md`「対処法の選択肢と判定基準」を参照して適用する。

## 分類判定の補助

品質基準の詳細は、`agent-basics:bugfix`の`references/ci-failure-handling.md`「分類判断のための詳細検討」を参照して適用する。

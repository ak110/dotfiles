# CI失敗の帰属と原因分析

CIの失敗について、原因箇所、セッションへの帰属、および再現性を実際の観測に基づいて分類し、根本原因に直接作用する対処を選択する。
push先のリモート、更新対象のref、runの監視、ならびに証拠の一時保存と後始末は、`agent-toolkit:commit`のpushおよびCIに関する規定に従う。

失敗ログから、自セッションの変更に起因する明白で単純な失敗（lint違反、フォーマッターの適用漏れ、期待値の単純な未追随など）であると確定できる場合は、「失敗の性質による分類」および「分類判定の補助」に定める競合仮説の評価を省略してよい。
ただし、`agent-basics:bugfix/references/root-cause-analysis-initial.md`「初動と拡張原因分析の判定」に基づく拡張原因分析の要否判定は省略しない（自セッションに起因するpush後のCI失敗は対象条件に該当する）。
修正commitが必要な場合の修正担当者と修正系列の扱いは、`agent-toolkit:process-wi/references/ci-fix-series.md`に従う。

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

## 失敗ログとartifactの取得

- 監視処理から取得したrun ID（GitLabの場合はpipeline ID）とjob IDを引き継ぎ、全体の実行状態を確認したうえで失敗ジョブのログを取得する。
  `gh`および`glab`コマンドの受付形式は、実行直前のヘルプで確認する
  - GitHubにおいてrun全体の進行中に対象ジョブが完了している場合は、`gh api repos/<OWNER>/<REPO>/actions/jobs/<job ID>/logs --allow-escape-sequences`を実行し、対象ジョブの全ログをmanaged-tempへ暫定保存する。
    端末エスケープシーケンスを含む応答は、このオプションを指定しないと取得が拒否される。
    ログが取得できない場合やジョブが進行中の場合は、アノテーションや失敗したテスト名など取得可能な証拠を用いて先行分析を進める
  - run全体の終了後は、`gh run view <run ID> --repo <OWNER>/<REPO> --json jobs`を実行して失敗したすべてのジョブのIDを確認し、各ジョブの全ログを同一のjob logs APIで保存する。
    `gh run view --log-failed`は、64KiBを超える長い行（巨大なパラメーターを含むテスト名など）が存在すると、それ以降の出力を欠落させたまま終了コード0で終了することがあるため、失敗ログの取得や失敗の有無の判定には使用しない。
    失敗ジョブ全体の確認は、`wait_ci.py`を再呼び出しして全体の完了結果を受け取ってから行う。
    待機の再開にあたっては、終了コード6の場合は同一引数で再実行し、終了コード2の場合は`agent-toolkit:commit`の`references/push-and-ci.md`「pushと監視」の終了コード2に関する規定に従う（1は完了したCIの失敗、3は待機処理自体の失敗）。
    同規定に従い、同一のSHAおよびbaselineまたは待機専用記録を維持したまま、`--timeout`を延長して再呼び出しする
  - 観測結果と再検証手順は、`docs/development/audit-records-commit-ci.md`にある。
    該当節は「agent-toolkit/skills/commit/references/ci-failure-handling.md：GitHubの状態別ログ取得：2026年10月1日」である。
    `--log-failed`による出力欠落の観測記録は、同ファイルの「agent-toolkit/skills/commit/references/ci-failure-handling.md：GitHubの状態別ログ取得：2026年10月24日」にある
- VRTの差分画像やtest-resultsなどのartifactも併せて取得する。
  artifactが存在しない失敗については、ログに記録された失敗テスト名、ステップ名、およびエラーメッセージから分類する
- 同一のコミットSHAを用いて、ローカル環境での再現テストを試みる

## 選択の流れ

**1.** 原因を取り違えないため、「失敗ログとartifactの取得」と、後述の分類節に沿って証拠を取得する。
非決定的な動作（フレーク）の疑いによる対象ジョブの再実行はplan mode開始前に行う。
失敗ジョブを検出した際は、計画モードの開始前に取得可能な証拠を暫定保存し、全体の完了を待たずにローカルでの再現確認、帰属の判定、および原因仮説の検討に着手する（待機時間を有効に分析へ充てるための努力目標）。
`wait_ci.py`を再開する場合は同一のbaselineを用い、SHA待機の場合は同一の`--failure-record`を使用する。
全体完了後に、失敗したすべてのジョブのログやartifactを先行分析の結果へ統合する。

**2.** 初回ログの内容と、同一SHAを用いたローカル再現の結果から、再現性を暫定的に分類する。

**3.** 失敗したジョブの全容を確認したうえで修正範囲を確定し、計画モードにおいて新しい計画ファイル（バグ）の調査工程として3つの観点（VRTの場合は差分の分類も含む）を記録する。
`agent-toolkit:process-wi`の公開工程では、公開工程が管理する原因分析結果へ記録する。

**4.** 自セッションに起因する失敗、または起因を否定できない失敗である場合は、自明な単純失敗を除き、初回ログの見え方にかかわらず競合仮説の支持・反証および判別実験を記録する。
`agent-basics:bugfix/references/root-cause-analysis-initial.md`「初動と拡張原因分析の判定」で分析の深さを選ぶ。
同資料に従い原因を確定し、類似箇所の見直しは`agent-basics:bugfix/references/root-cause-remediation.md`、処置の階層は`agent-basics:bugfix/references/root-cause-actions.md`に従って確定する。

**5.** 環境要因、または自セッションの変更と無関係であると分類した場合は、`atk wi list --type=awi --status=active`で同一事象を扱うAWIを検索する。
該当するAWIが存在する場合は、`atk wi show <filename> --skip-pull`で本文に記載された緩和策を確認し、前述の対処選択肢と同じ階層で比較検討する。

**6.** 選択した対処、基準情報、CIの生の観測事実、および分析結果を引き継ぎ、計画ファイル（バグ）の初版を起草する。
公開工程においては、公開担当がこれらの情報をCI記録として保持し、CI修正担当へ引き渡す（`agent-toolkit:process-wi/references/ci-fix-series.md`）。

## 失敗の性質による分類

`agent-basics:bugfix/references/ci-failure-handling-classification.md`「失敗の性質による分類」を適用する。

## 対処選択肢と判定基準

`agent-basics:bugfix/references/ci-failure-handling-classification.md`「対処法の選択肢と判定基準」を適用する。

## 分類判定の補助

`agent-basics:bugfix/references/ci-failure-handling-investigation.md`「分類判断のための詳細検討」を適用する。

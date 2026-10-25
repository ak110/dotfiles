# CI失敗の帰属と原因分析

CI失敗の原因箇所、セッション帰属、再現性を実際の観測で分類し、原因に作用する対処を選ぶ。
push先、更新ref、run監視、証拠の一時保存と後始末は`agent-toolkit:commit`のpushとCIの規定に従う。

失敗ログから自セッションが導入した自明な単純失敗（lint違反、formatter未適用、単純な期待値の未追随など）と確定できる場合は、「失敗の性質による分類」と「分類判定の補助」の競合仮説の評価を省いてよい。`agent-basics:bugfix/references/root-cause-analysis.md`「初動と拡張原因分析の判定」による拡張原因分析の要否判定は省かない（自セッション帰属のpush後のCI失敗は条件の例に当たる）。修正commitが要る場合の修正者は「修正commitが必要なCI失敗を修正するエージェント」で決める。

## 名指し参照の読込方法

本文の各参照の適用条件を保ち、選んだ資料の指定範囲を取得してから、その基準を使う具体的な判断へ進む。起動時の行の資料は起動本文とともに届く。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../commit/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/commit/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:bugfix/references/root-cause-analysis.md#「初動と拡張原因分析の判定」` | 判断 | `section:agent-basics:*/skills/bugfix/references/root-cause-analysis.md#「初動と拡張原因分析の判定」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../commit/references/push-and-ci.md#「pushと監視」` | 判断 | `section:agent-toolkit:*/skills/commit/references/push-and-ci.md#「pushと監視」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:bugfix/references/ci-failure-handling.md` | 判断 | `read:agent-basics:*/skills/bugfix/references/ci-failure-handling.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../process-wi/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:bugfix/references/root-cause-analysis.md` | 判断 | `read:agent-basics:*/skills/bugfix/references/root-cause-analysis.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../../share/exec.parent.md` | 判断 | `read:agent-toolkit:*/share/exec.parent.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../review-standards/references/exec-review-recording.md#「受理済み判定への参照」` | 判断 | `section:agent-toolkit:*/skills/review-standards/references/exec-review-recording.md#「受理済み判定への参照」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:bugfix/references/ci-failure-handling.md#「失敗の性質による分類」` | 判断 | `section:agent-basics:*/skills/bugfix/references/ci-failure-handling.md#「失敗の性質による分類」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:bugfix/references/ci-failure-handling.md#「対処選択肢と判定基準」` | 判断 | `section:agent-basics:*/skills/bugfix/references/ci-failure-handling.md#「対処選択肢と判定基準」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:bugfix/references/ci-failure-handling.md#「分類判定の補助」` | 判断 | `section:agent-basics:*/skills/bugfix/references/ci-failure-handling.md#「分類判定の補助」` |

## 失敗ログとartifactの取得

- 監視で得たrun ID（GitLabではpipeline ID）とjob IDを引き継ぎ、全体の状態を確かめてから失敗jobのログを取得する。`gh`と`glab`の受理形式は実行直前のヘルプで確かめる
  - GitHubでrun進行中に対象jobが完了済みなら、`gh api repos/<OWNER>/<REPO>/actions/jobs/<job ID>/logs --allow-escape-sequences`で全jobログをmanaged-tempへ暫定保存する。端末エスケープを含む応答はこの指定が無いと拒否される。取得できない場合とjob進行中は、annotationや失敗テスト名など取得できる証拠で先行分析する
  - run終端後は`gh run view <run ID> --repo <OWNER>/<REPO> --json jobs`で全失敗jobのIDを確かめ、各失敗jobの全jobログを同じjob logs APIで保存する。`gh run view --log-failed`は64KiBを超える行（大きなパラメーターを含む試験名など）で以後の出力を欠落させても終了コード0で終わる。このため失敗の取得と不在の判断に使わない。全失敗jobの確認は`wait_ci.py`を呼び直して全体終端の返却を受けてから行う。待機の再開は、終了コード6では同じ引数で、2では`agent-toolkit:commit`の`references/push-and-ci.md`「pushと監視」の終了コード2の箇条に従う（1は終端のCI失敗、3は待機手段の失敗）。同箇条は同じSHAとbaselineまたは待機専用記録を保ったまま`--timeout`を増やして呼び直すことを定める
  - 観測結果と再検証手段は`docs/development/audit-records.md`の「agent-toolkit/skills/bugfix/references/ci-failure-handling.md：GitHubの状態別ログ取得：2026年10月1日」にある。`--log-failed`の欠落の観測は同ファイルの「agent-toolkit/skills/bugfix/references/ci-failure-handling.md：GitHubの状態別ログ取得：2026年10月24日」にある
- VRT差分画像やtest-resultsなどのartifactも取得する。artifactの無い失敗はログの失敗テスト名・ステップ・エラーメッセージで分類する
- 同一SHAでローカル再現を試す

## 品質の分類

失敗の性質による分類、対処選択肢と判定基準、分類判定の補助は`agent-basics:bugfix`の`references/ci-failure-handling.md`を全文読んで適用する。

## 選択の流れ

非決定性の疑いを診断するための対象jobの再実行は、plan mode開始前に行う。

1. 失敗jobを検出したら、plan mode開始前に取得できる証拠を暫定保存し、全体の終端を待たずにローカル再現、帰属判定、原因仮説の検討を始める（努力目標。待機時間を分析へ充てるため）。`wait_ci.py`の再開には同じbaseline、SHA待機では同じ`--failure-record`を使い、全体終端後に失敗集合全体のログとartifactを先行分析へ統合する
2. 初回ログと同一SHAの再現結果から再現性を暫定分類する
3. 全失敗集合を確かめてから修正範囲を確定し、plan modeで新しい計画ファイル（バグ）の調査工程として3観点（VRTでは差分分類も）を記録する。`agent-toolkit:process-wi`の公開工程では、公開工程が保持する原因分析結果へ記録する
4. 自セッション帰属か帰属を否定できない場合は、自明な単純失敗を除き、初回ログの見え方を問わず競合仮説の支持・反証・判別実験を記録する。そのうえで`agent-basics:bugfix/references/root-cause-analysis.md`「初動と拡張原因分析の判定」と`references/root-cause-analysis.md`に従って原因、類似見直し、処置の階層を確定する
5. 環境起因か自セッションと無関係と分類した場合は、`atk wi list --type=awi --status=active`で同じ事象を扱うAWIを探し、あれば`atk wi show <filename> --skip-pull`で本文の緩和策を前掲の対処選択肢と同じ階層で比べる
6. 選んだ対処と、基準情報、CIの生の観測事実、分析結果を引き継いで計画ファイル（バグ）の初版を起草する。公開工程では終端担当が同じ情報をCI記録として保持し、CI修正担当へ渡して主作業ツリーで修正させる

## 修正commitが必要なCI失敗を修正するエージェント

原因分析で修正commitが必要と確定したCI失敗は、本節が定めるエージェントが修正する（元計画の実行工程への再投入はしない）。
本節を適用するエージェントが対象worktreeの書込所有権を持ち、そこへ書き込む稼働中の実装担当が無ければ自ら修正する。そうでなければ`CI修正担当`を1件起動する（起動と入力は`${CLAUDE_PLUGIN_ROOT}/share/exec.parent.md`）。

`CI記録`は原因分析結果、修正認可根拠、対象の検証結果、変更目的、対象、公開契約および入力計画の7項目とする。

- 修正認可根拠: 計画が記録した元の要求か確認回答の該当箇所、原因となった変更を認可したユーザー指示の逐語文、または既存の公開契約の該当箇所。特定できなければ修正担当を起動せずユーザー確認する
- 原因確定のため診断コードを加える場合は、「原因分析結果」で競合する仮説ごとに仮説を分ける観測を、「公開契約」で採取する対象と属性を対応付ける。全件の列挙や走査はその判別に要る場合だけ含め、大きければ一時領域への保存と範囲の読取を使う（完全性を失う容量上限は新設しない）。元の失敗と終了コードは保持し、採取の失敗は別に記録する
- レビュー基準は、原因commit自身に対応する計画があればその計画、無ければCI記録とする（処理対象WIの別の計画は使わない）
- 計画を持たないCI修正commitが再push後の別の失敗の原因になった場合は、直前のCI記録を継承して7項目を更新する。CI対応レビュー指摘管理表があれば、そのパスと`修正系列の開始時のHEAD`も継承する

修正者は原因修正、全検証、差分検収、stage、commitまでを完了する。修正前に起草する計画は「選択の流れ」が決め、公開工程の外で自ら修正するエージェントは手順6の計画に従う。

### 修正系列

計画を基準とする後続の実行レビューには、計画の進捗ログに保存された読取可能な受理済みの実行レビュー返却のファイルの絶対パスを任意入力`受理済みの完成条件証拠`として渡す（無ければ`なし`）。使える条件は`agent-toolkit:review-standards`の`references/exec-review-recording.md`「受理済み判定への参照」に従う。受領確認を終えた新しい返却の所在も同じ進捗ログへ保存し、修正系列の後続起動から到達できるようにする。

最初のCI失敗（公開工程では全体検証の失敗を含む）からCIの成功か工程の終端までを「修正系列」と呼ぶ。修正の回数、CI記録、レビュー指摘管理表、`修正系列の開始時のHEAD`はこの単位で引き継ぎ、原因commitが変わっても回数を継続する。回数は是正した失敗に対応する修正の数で数え、原因commitへ取り込んだ修正（`agent-toolkit:commit`の`references/push-and-ci.md`「pushと監視」の「原因commitへ取り込む区分」）も1回とする。取り込んだ場合、`exec-review`へ渡す`原因commitOID`は書き換え後の履歴で解決できる値とし、書き換え前後のOIDの対応と`git range-diff`の結果をCI記録へ残す。

修正系列を終えるCIの成功は、失敗したjobが判定対象に含まれ期待する終了状態（許容された失敗を含む）で終わったpipeline（GitHub Actionsではrun）に限る。push先でそのjobが実行されない場合（保護branchだけ、schedule、パス条件など）は、jobの実行器を含めたローカル再現（「分類判定の補助」）で確かめる。それもできなければ、未確認のjobと試した再現と残る条件差をエスカレーションとして返す。

`入力計画`には原因commitに対応する計画のパスを渡す。

- 保存済みの計画は、起動するエージェントが起動前に`atk plans checkout <private-notes/plans/からの相対パス>`で`~/.claude/plans`へ取得し、そのパスを渡す。保存済みのパスを直接渡すと進捗ログと指摘管理表への記録が拒否される。同じ修正系列の実行レビュー（`レビュー基準: 計画`）へも同じパスを渡す
- 未保存なら`~/.claude/plans`のパスをそのまま渡し、対応する計画が無ければ`なし`とする
- 修正系列の終端で、起動したエージェントが`atk plans commit <private-notes/plans/からの相対パス>`で再保存し、成功の表示と警告の不在を確かめる。CI記録を基準とする`ci-<修正系列の開始時のHEAD>.exec-review.tsv`も同じ時機に`atk plans commit ci-<修正系列の開始時のHEAD>.exec-review.tsv`で保存する。修正系列が続く間（再pushの結果待ちを含む）は表を`~/.claude/plans`に残し、先に保存した場合は`atk plans checkout ci/ci-<修正系列の開始時のHEAD>.exec-review.tsv`で戻してから続ける

同じ修正系列の3回目以降の修正と、回数によらず公開契約または設計へ及ぶ修正では、単一の`exec-review`を収束まで反復する（公開工程の修正系列にも適用する）。それ以外は`exec-review`を起動せず再push、CI確認へ戻る。2回目までの修正を修正者自身の確認で収束させ、3回目以降でレビューを起動するのは、3回目が要ることが直前の修正が原因を捉えていないことを示すためである。CI対応レビュー指摘管理表の作成・継続・保存は`exec-review`を起動する場合だけ行う。
`exec-review`を起動しない修正では、失敗ログの該当箇所を要約せずに読んで、失敗した検証が参照する実装か期待値の現物と比べて直接的原因を確定する。修正後はその検証を同じ条件で再実行して期待する終了状態を確かめ、影響し得る他のチェックも再実行する。
外部基盤障害など修正commitを要しない失敗では実装担当を起動しない。

### 公開工程の終端担当が扱うCI修正

`agent-toolkit:process-wi`の公開工程のCI修正は公開工程の中で完了させ、新しい計画ファイル（バグ）は作成しない。
終端担当は7項目のCI記録を保持し、主作業ツリーを対象とする`CI修正担当`へ修正させる（本節冒頭の、自ら修正する一般則の例外。手順は`${CLAUDE_PLUGIN_ROOT}/share/session-termination.subagent.md`。所在参照）。
全体検証の失敗も同じ修正系列に数え、`exec-review`の起動条件と起動しない修正の前提は「修正系列」と同じとする。
各修正commitの後に版数とmanifestを再判定して再push、CI確認へ戻り、再push後の失敗では新しい原因commitについて対応する計画の有無を判定し直す。

## 失敗の性質による分類

品質の定義は`agent-basics:bugfix`の`references/ci-failure-handling.md`「失敗の性質による分類」を適用する。

## 対処選択肢と判定基準

品質の定義は`agent-basics:bugfix`の`references/ci-failure-handling.md`「対処選択肢と判定基準」を適用する。

## 分類判定の補助

品質の定義は`agent-basics:bugfix`の`references/ci-failure-handling.md`「分類判定の補助」を適用する。

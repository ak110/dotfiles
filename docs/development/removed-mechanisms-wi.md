# 撤去した仕組み: WIとキュー

## WIとキュー

| 働き | 撤去 | 理由 | 方針の所在 | 再導入の条件 |
| --- | --- | --- | --- | --- |
| WI保存前に口語・ダッシュを走査し、警告と表現だけの推敲を求める | 2026-11-10 | 中間成果物の意味・要求達成を変えない表現の確認が往復を生むため撤去する。 | 意味・認可・必要事項を検収し、転記先で成果物の品質基準へ従う。docs/development/concepts-wi-drafting.md「AWI・UWIの本文と投入」 | 撤去理由を解消できることを示した場合 |
| 完了報告の「要対応」などの見出しへ、ユーザーの判断・行動依頼を集める | 2026-10-10、commit: eb7c66244 | 判断・行動の依頼を報告見出しへ集める運用をユーザーが廃止した。 | 判断が必要な事項は個別UWIへ置く。docs/development/concepts-confirmation-format.md「確認の提示形式」 | 撤去理由を解消できることを示した場合 |
| 自由記述だけの回答形式で新規UWIへの回答を求める | 2026-10-06、commit: 7d49b7fad | 独立した問いを束ねた自由記述の確認が回答負担と未合意を生んだ。 | 独立した問いごとに選択肢形式のUWIを作る。docs/development/concepts-confirmation-format.md「確認の提示形式」、docs/development/concepts-wi-drafting.md「AWI・UWIの本文と投入」 | 撤去理由を解消できることを示した場合 |
| AWIの種類と専用変換操作でWIを計画へ変える | 2026-09-13、commit: 5c6e77a69 | 計画を1ファイル書式にまとめ、計画型AWIと変換を廃止する判断による。 | 新規の計画変換を行わず、保存済みplan_fileは読取互換とする。docs/development/concepts-planning-history.md「計画レビュー廃止前の運用（2026年9月13日まで）」、docs/development/concepts-wi-states.md「経緯（WIの状態と遷移）」 | 撤去理由を解消できることを示した場合 |
| batch由来AWIへ24時間の冷却を課し、WI画面の冷却解除ボタンと専用APIで解除する | 2026-10-30、commit: 7684b34dc | 冷却解除が取り消せないというユーザーの指摘により要確認へ置き換えた。 | 通常の期限待ちと認可待ちを分け、batch固有の冷却操作を置かない。docs/development/concepts-session-review.md「`review-session`」 | 撤去理由を解消できることを示した場合 |
| batch由来AWIを内容確認まで待たせ、確認ボタンで処理可能にする | 2026-10-31、commit: f017b41fb | batch由来AWIの内容確認待ちを撤去するユーザー要求による。 | 由来に応じて採否を検討し、内容確認を自動処理の前提にしない。docs/development/concepts-session-review.md「`review-session`」、docs/development/design-session-review-completion.md「完了報告と振り返りの関係」 | 撤去理由を解消できることを示した場合 |
| WIの一括取得ごとに個別の一時領域を作成し、取得後にcleanupする手順 | 2026-09-26、commit: 6d64a4604 | 取得のたびの一時領域作成と回収が煩雑だった。 | 一括取得の保存物は7日の自動削除へ委ねる。docs/development/concepts-wi-processing.md「WI選定と処理」 | 撤去理由を解消できることを示した場合 |
| 自動処理から除外する状態を、用途別に`planning`と`editing`へ分ける | 2026-09-04、commit: 89b1f128f | 同じ自動処理除外の振る舞いを用途別に分け、状態定義が分岐していた。 | 自動処理からの除外はholdへ統合する。docs/development/concepts-wi-states.md「WIの状態と遷移」、docs/development/design-wi-listing.md「キュー状態と公開一覧」 | 撤去理由を解消できることを示した場合 |
| WI保存後に本文を再取得・全文比較し、一致判定の固定出力も確認する手順 | 2026-09-07、09-16、commit: c3844da39 | 保存本文の再掲と固定の一致出力がトークンを消費し、確認工程を残す利益が無かった。 | 保存一致はCLI内で検査し、呼出側は終了コードで確認する。docs/development/concepts-workflows.md「`atk`の出力と操作」 | 撤去理由を解消できることを示した場合 |
| WIの進行を一律に停止し、依存関係による待機を代替する | 2026-08の事例、commit: 未特定 | 一律停止に伴う本文追記で空本文を保存する欠陥が誘発された。 | 先行成果待ちはdepends_onへ一本化する。docs/development/incidents-data-security.md「データ破壊と喪失」 | 撤去理由を解消できることを示した場合 |
| 全レーン終了後にready一覧を再取得し、追加対象を処理する | 2026-08-29、commit: 789bdc6f3 | 後続のエージェント判断が起動時に固定された処理対象を変更していた。 | 起動時の固定集合の終了後は追加候補を取り直さない。docs/development/incidents-stagnation.md「停滞と空転」 | 撤去理由を解消できることを示した場合 |
| 単一項目の自然発生条件を待つため、選定の別集合・frontmatter状態・公開CLIを設ける | 2026-10、commit: 83e676bf0 | 自然発生待ちで1件が無期限に残り、全実行へ専用分岐の読込を課した。 | 起動条件は制御した実行で検証して終了する。docs/development/design-wi-addition.md「処理中の処理対象WIの追加と反映後の観測」 | 撤去理由を解消できることを示した場合 |

# Codexセッションの終了の設計記録

[設計記録の索引](design.md)と[継続処理の起動・隔離・制御の設計記録](design-process-loop.md)から参照する主題別の記録である。

## Codexのprocess-loopセッションの終了

起動目的は初回・再開、manifestの有無によらず、統括セッション自身が担当するprocess-wi all-reposの工程の完遂を示す。
目標の評価は目的文の字義で達成を判定するため、正当な先送りを結果へ記録した終了を完遂に含め、統括の終了後に常駐が行う検収を含まないことを短く併記する。
スキル全体の完遂だけを示した目的文では、評価が常駐の検収や先送りした項目まで統括の未達と解釈し、正当な先送りで終える回の終了を繰り返し拒否した（2026年11月6日の観測）。
集合JSONと可変入力は`AGENT_TOOLKIT_SESSION_START_INPUT`からSessionStartへ渡し、機械生成の出所を付けて配送する。
成功・正当な未完了終了・回復可能な残工程の判断はall-repos「結果と終了」が所有し、目的文へ恒久的な終了細則を再掲しない。
ホストごとの目的入力の違いと終了能力の判定は保持する。
目的文にmanifestや終了手順を連結する案は、反復評価の対象と更新箇所を増やすため採らない。

ready項目がなくなり公開工程の検収を終えると、`agent-toolkit:report-completion`が元の作業の完了報告を送り、続いて`atk agents-exit-session`が`/goal`で登録した目的とセッションを終了する。
振り返りはSessionStart/SessionEndを契機に起動する`agent-toolkit:review-session batch`が、非活動になったセッションを非同期に分析する（`design-session-review-completion.md`「完了報告と振り返りの関係」）。
その結果は作業完了報告とは独立して届ける。
起動時の終了能力probeと、停止要求の直前に終了対象を新規識別する扱いは`design-workflow-termination.md`「セッションの終了」にある。
Linuxでremote-controlを使わない直接CLIを終了対象として確認できた場合は、Codexが自律終了して親の監視ループへ戻る。
終了対象を確認できない環境では対話UIに終了案内を表示し、ユーザーが`/exit`を入力すると親の監視ループへ戻る。

親は自身が起動したCodexの子のcwd・開始時刻からロールアウト記録を対応付ける。
上限による失敗の後に新turnが無い場合だけ、その所有する子へ終了を要求し、正常終了として次の反復へ進む。
新turnがあれば子を保持する。
次の反復は可用性判定で解除を待ち、新しい会話でprocess-wiを起動する。
親の`process_loop_session.py`は子の終了後に`lane.cleanup_session`で回収を試み、未統合・dirty・稼働中・確認不能などのレーンは所有登録と成果を保持する。
実体が全て無い登録は同じ処理が回収するため、固定作成先を次のprocess-wiが再利用できる。
レーンのbranch・worktree・選定原本・旧所有sessionは、`lane.py`が状態ディレクトリの`lanes/<旧所有session>/lane-*.json`へ保存する所有登録が持つ。
所有登録は選定原本から写したレーンのWI集合と統合状態、rebase後の対応表と検証記録も持つため、managed-tempにある選定原本が失効した後の再開と回収も同じ所有登録から判定する。
状態ディレクトリの絶対パスは`atk config get state_dir`で取得する。
WIの本文と`processing`の状態はprivate-notesのキュー、計画と進捗ログは`~/.claude/plans`が保持する。
新しい会話のprocess-wiは[選定手順](../../agent-toolkit/skills/process-wi/references/selection-procedure.md)に従い、`atk lane delete --list`で残存登録と回収結果を読み、WIに対応する計画を`atk plans list`から解決する。
再開する工程は計画の進捗ログから決め、[中断レーンの再開](agent-toolkit:process-wi/references/run-lanes-integration.md)に従って新しいレーン担当へ計画と既存worktree・branch・現在HEADを渡す。
資源を再利用するときは旧所有主体の終了を確認し、`atk lane create`の`--reuse-record <record_path>`へ所有登録のパスを渡して新しい所有sessionへ移す。
この再開は保存した状態からの復元であり、親は中断した会話履歴を新しい会話へ複製しない。
対応する計画や所有を一意に確定できない場合は、既存の再開手順が続行できない理由を返す。
`codex resume`で同じ対話会話を起動する案は、`usage_limited`のgoalの再開確認で無人の子が停止するため採らない。
共有daemonへの`codex queue`を使う案も、effortを指定する`-c`付きの起動がdaemonの対象外であるため採らない。
ロールアウトの判定は共通層、子の終了は`process_loop_session.py`が所有する。
最上位TUIの自動再開とは適用範囲を分ける。

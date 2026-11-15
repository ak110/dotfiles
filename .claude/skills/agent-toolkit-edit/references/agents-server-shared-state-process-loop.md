# agents_serverの共有状態と読み書きの担当：process-loopとレーン

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

## process-loopとレーン

| 共有状態 | 基準となる保持先 | 読む主体 | 更新できる主体 |
| --- | --- | --- | --- |
| ホストsessionの活動記録（`session-activity/`配下の会話別・WI投入作業・報告用要約の3種） | `session-activity/`配下 | `_common/session_activity.py` | 保存先、作成・更新・削除の主体と契機は`session-state-and-flags-activity.md`が定める |
| 独立したWI投入の受付・移管票・実行結果 | `wi-submit/requests/<request_id>.json`と`wi-submit/runs/<run_id>/` | `atk wi submit --show`、既存SessionStart/SessionEndの再観測、process-loopの対象別結果の受付票検証 | `_atk/wi/submit.py`。自己完結した要求を原子的に保存し、独立監督のPID世代と所有移管、finishの要求別処置、run-skillの報告同期を対応付ける。未完了・未報告を保持し、完了・同期済みだけを30日後に回収する。製品worktree・process-loopの所有を引き継がない |
| origin単位の処理所有と未完了の再開情報 | `process-loop-ownership/<originのSHA-256>.json`および同名の`.lock` | 専用process-loopおよびall-repos | `_atk/wi/process_loop_ownership.py`。短時間のファイルロックを取得して所有権トークンと生存状態を確認し、worktree、Git共通ディレクトリ、担当セッション、レーン所有セッションの情報を更新する |
| 対象別の担当返却 | `process-loop-ownership/<originのSHA-256>.result.json` | all-reposの中継処理および再開処理 | `_atk/wi/process_loop_all_repos.py`。担当エージェントの終了を確認した後、完了済み工程と残存工程の情報を保存する |
| 担当が進捗なしで終えた起動入力（無進捗記録） | `process-loop-no-progress/<originのSHA-256>.json` | 専用process-loopおよびall-reposの起動判定処理 | `_atk/wi/process_loop_launch_gate.py`。担当エージェントが進捗なしで終了した時点の起動入力を対象リポジトリごとに保存する。スキップせずに担当エージェントを起動した時点、または起動入力が空になった時点で削除する。この記録はホストごとの一時的な制御用であり、WI自体のステータスには影響を与えない |
| Git統合と計画保存・WI終了の進行 | `lanes/<所有session>/<lane>.json`の`integration`セクション | `atk lane integrate`、および回収可否の判定処理 | `_atk/lane.py`。Git統合の進行状況と成功した最終化処理の記録を保存し、失敗時には残りの工程のみを再開できるようにする。最終化処理の最初のステップを開始する前に確定値（統合後HEADの短縮OID、完了時刻、レーン稼働秒数）を生成して統合状態へ保存し、最初に受け付けた`lane_started_at`を登録の最上位へ保存する（再開時に再生成しない）。すべての最終化処理と、対象ファイル群および統合状態の整合性確認が成功した段階で統合完了を記録する。rebase対応表はmanaged-temp内に保存され、レーン回収後も同一の保持期限内であれば参照できる |

`_common/session_parents.py`と`_common/session_activity.py`は、前述のセッション登録簿を読み取り、保存されている委譲元情報やプロセス識別情報を参照してアクティビティを判定する。
MCPサーバーの`SessionState`やセッション登録簿自体の更新は、これら両モジュールの担当外である。

レーンリソースの所有登録や回収結果への参照情報は、同じ`state_dir`配下の`lanes/<所有session>/`に個別に配置する。
`_atk/lane.py`が作成、明示的な再利用、ならびに回収処理を一括して管理し、通常の終了時やprocess-loopの子プロセス終了後も同一の処理を利用する。
レーン管理側は、`_common/session_registry.py`の`resolve`による解決結果（実行中かどうかとcwd）を通じてセッション登録簿を読み取るのみにとどめ、状態の更新や終了登録はMCPサーバー側が担う。
回収された結果データは専用worktree外のmanaged-tempへ保存され、既存の保持期限ポリシーに従って破棄される。
なお、未解決のリソース登録は破棄せず保持し、回収処理が削除するのは解決済みのレーン登録のみとする。

## 対象別workerと再開予約

持続する`process_loop.py`のschedulerが、対象別workerのPID世代と実行中の集合を保持する。
通常は1対象1worker、旧集合会話の再開では元会話の対象群を1つの実行単位にする。
対象別の候補・所有・lane_session_idの保存先は上表と同じで、独立した完了台帳は設けない。

| 状態 | 保存先 | 読む主体と更新する主体・寿命 |
| --- | --- | --- |
| workerの凍結入力・結果・標準出力/標準エラー | managed-tempの`process-loop-worker-*`配下 | `process_loop_scheduler.py`が入力とログを作成し、別プロセスのworkerが検収後の対象別結果を原子的に保存する。schedulerが終了・部分結果・対象を検査し、既存の7日保持に従う |
| 次回再開予約 | state_dirの`process-wi-resume.json`と同名stemの`.lock` | `process_loop_control.py`のresume/cancelが保存・置換・取消し、statusと常駐が読む。予約・abort操作と実Popenを同じ有限ロックで排他する。実起動成功だけが世代一致の予約を消費し、準備・更新・起動失敗と中断では保持する |
| 次回の追加指示 | state_dirの`process-wi-instructions`と`.lock` | `process_loop_log.py`の追加・取消と実起動が同じ有限ロックで更新する。実Popen前にmanifestへ反映し、起動成功後に1回消費する |
| picker等で選択した実ID/cwdと所有確定のACK | managed-tempの`startup-identity-*`配下 | 起動側がnonce・所有PID世代と応答先を既存SessionStart入力へ載せ、hookが実ID/cwdを返す。起動側が選択対象の所有・manifestを確定してACKする。hookは所有側の生存と120秒期限を確認し、実行の保護と7日保持に従う |
| 非対話Codex/Antigravityの実IDを含むJSON出力 | managed-tempの`process-loop-stream-*`配下 | `process_loop_session.py`が通常ファイルへstdoutを保存し、既存のinit/thread.started解析で実IDを取得する。読み取り用FDは別に開き、パイプのEOF待ちや読取スレッドを追加しない。出力の保存先をworkerログへ渡し、7日保持に従う |

CLI終了後のレーン検収はintegration.phaseが`finalized`のものだけを完了として受理する。
`git-integrated`は後処理が残る中間状態として保持する。
報告前のstatus検収も同じ`_remaining_work`と開始側gateを使う。
レーン管理が登録簿へ書く権限は追加せず、process-loopが起動・観測する本体CLIの公開と、agents_serverの委譲turnの公開を区別する。

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

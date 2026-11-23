# agents_serverの共有状態と読み書きの担当：process-loopとレーン

## process-loopとレーン

| 共有状態 | 基準となる保持先 | 読む主体 | 更新できる主体 |
| --- | --- | --- | --- |
| ホストsessionの活動記録（`session-activity/`配下の会話別記録） | `session-activity/`配下 | `_common/session_activity.py` | 保存先、作成・更新・削除の主体と契機は`docs/development/internals-session-state-activity.md`が定める |
| 独立したWI投入の受付・移管票・実行結果 | `wi-submit/requests/<request_id>.json`と`wi-submit/runs/<run_id>/` | `atk wi submit --show`、既存SessionStart/SessionEndの再観測、process-loopの対象別結果の受付票検証 | `_atk/wi/submit.py`。自己完結した要求を原子的に保存し、独立監督のPID世代と所有移管、finishの要求別処置、run-skillの報告同期を対応付ける。未完了・未報告を保持し、完了・同期済みだけを30日後に回収する。製品worktree・process-loopの所有を引き継がない |
| Git統合と計画保存・WI終了の進行 | `lanes/<所有session>/<lane>.json`の`integration`セクション | `atk lane integrate`、および回収可否の判定処理 | `_atk/lane.py`。Git統合の進行状況と成功した最終化処理の記録を保存し、失敗時には残りの工程のみを再開できるようにする。最終化処理の最初のステップを開始する前に確定値（統合後HEADの短縮OID、完了時刻、レーン稼働秒数）を生成して統合状態へ保存し、最初に受け付けた`lane_started_at`を登録の最上位へ保存する（再開時に再生成しない）。すべての最終化処理と、対象ファイル群および統合状態の整合性確認が成功した段階で統合完了を記録する。rebase対応表はmanaged-temp内に保存され、レーン回収後も同一の保持期限内であれば参照できる |

`_common/session_parents.py`と`_common/session_activity.py`は、前述のセッション登録簿を読み取り、保存されている委譲元情報やプロセス識別情報を参照してアクティビティを判定する。
MCPサーバーの`SessionState`やセッション登録簿自体の更新は、これら両モジュールの担当外である。

レーンリソースの所有登録や回収結果への参照情報は、同じ`state_dir`配下の`lanes/<所有session>/`に個別に配置する。
`_atk/lane.py`が作成、明示的な再利用、回収を管理する。
通常の巡回では統括が終了前に同じ回収処理を使い、異常終了で残った資源は次の統括が判定する。
レーン管理側は、`_common/session_registry.py`の`resolve`による解決結果（実行中かどうかとcwd）を通じてセッション登録簿を読み取るのみにとどめ、状態の更新や終了登録はMCPサーバー側が担う。
回収された結果データは専用worktree外のmanaged-tempへ保存され、既存の保持期限ポリシーに従って破棄される。
なお、未解決のリソース登録は破棄せず保持し、回収処理が削除するのは解決済みのレーン登録のみとする。

## 再開予約と追加指示

巡回用CLIは対話セッションを起動し、対象別の候補・選定・処置と検収はセッション側の記録へ保つ。

| 状態 | 保存先 | 読む主体と更新する主体・寿命 |
| --- | --- | --- |
| 次回再開予約 | state_dirの`process-wi-resume.json`と同名stemの`.lock` | `process_loop_control.py`のresume/cancelが保存・置換・取消し、statusと常駐が読む。予約・abort操作と実Popenを同じ有限ロックで排他する。実起動成功だけが世代一致の予約を消費し、準備・更新・起動失敗と中断では保持する |
| 次回の追加指示 | state_dirの`process-wi-instructions`と`.lock` | `process_loop_log.py`の追加・取消と実起動が同じ有限ロックで更新する。実起動の引数へ渡し、起動成功後に1回消費する |
| picker等で選択した実ID/cwdと所有確定のACK | managed-tempの`startup-identity-*`配下 | 起動側がnonce・所有PID世代と応答先を既存SessionStart入力へ載せ、hookが実ID/cwdを返す。起動側が選択対象の所有・manifestを確定してACKする。hookは所有側の生存と120秒期限を確認し、実行の保護と7日保持に従う |
| 非対話Codex/Antigravityの実IDを含むJSON出力 | managed-tempの`process-loop-stream-*`配下 | `process_loop_session.py`が通常ファイルへstdoutを保存し、既存のinit/thread.started解析で実IDを取得する。読み取り用FDは別に開き、パイプのEOF待ちや読取スレッドを追加しない。出力の保存先をworkerログへ渡し、7日保持に従う |

統括はセッション終了前のレーン検収で、integration.phaseが`finalized`のものだけを完了として受理する。
`git-integrated`は後処理が残る中間状態として保持する。
対象ごとの結果と残工程は統括が検収して完了報告に書き、ファイルへは保存しない（[登録全体のAWI処理の結果と終了](../../agent-toolkit/skills/atk-mode/playbooks/process-wi-all-repos-results.md)）。
レーン管理が登録簿へ書く権限は追加せず、process-loopが起動・観測する本体CLIの公開と、agents_serverの委譲turnの公開を区別する。

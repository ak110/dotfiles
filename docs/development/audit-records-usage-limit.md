# 監査記録: 利用上限と再開

[監査記録の索引](audit-records.md)に収録対象と記録の書式を示す。

## agent-toolkit/skills/atk-mode/references/usage-limit.md：利用上限後の再開：2026年10月14日

- 観測日: 2026年10月14日（起草時の観測報告に基づく記録）
- 観測した版数: Claude Code 2.1.299
- 再検証手段: [Interactive mode](https://code.claude.com/docs/en/interactive-mode#wait-for-a-usage-limit-to-reset)でRemote Controlと`-p`の対象範囲を確認する。
  手元に保存したRemote Control接続のメイン記録を、下記の公開操作で照会する。
  通知冒頭、解除予定時刻と解除後の新turnの有無を対応付ける。
  起草時の報告は猶予通知後にユーザーの発話で再開した経過を持つ。
  元のsession記録はリポジトリ外の私有記録であり、同梱していない。
  自然発生する上限の実機再現は今回の文書同期の対象外とした

`<対象session ID>`を対象のClaude session IDに置き換えて、猶予通知の記録位置を取得する。
IDは対象の会話で実行するシェルの`CLAUDE_CODE_SESSION_ID`から確認できる。
そのsessionのtranscriptを保存しているOSアカウントで次のコマンドを実行する。

```sh
atk run-script session-review-evidence -- --claude-session-id '<対象session ID>' --fixed-string 'Usage limit reached; a short grace allowance remains'
```

`<record>:<line>`を返却された`record`と`line`の組に置き換えて、同じsession IDで全文を取得する。
拒否と解除後のturnの時系列は、照会オプションを付けずに同じsessionを取得して確認する。

```sh
atk run-script session-review-evidence -- --claude-session-id '<対象session ID>' --detail '<record>:<line>'
atk run-script session-review-evidence -- --claude-session-id '<対象session ID>'
```

元のtranscriptの絶対パスが分かる場合は、`--claude-session-id`の指定を`--transcript '<transcriptの絶対パス>'`へ置き換えられる。
引数の定義と記録が見つからない場合の診断は`atk run-script session-review-evidence -- --help`で確認する。

## agent-toolkit/skills/atk-mode/playbooks/process-wi-all-repos.md：登録集合：2026年11月12日

- 観測日: 2026年11月12日
- 観測した版数: agent-toolkit 2.257.0、実装commit 784f3126c
- 当時の再検証手段: `_atk/wi/process_loop.py`の`_run_coordinator`・`cmd_process_loop`と`process_loop_session.py`の`_run_cli_session`・`_stop_orchestrator`を読む。
  更新が統括起動前に1回行われる条件、通常終了後の結果・writer検収、明示resumeの初回消費、利用上限と例外時の子孫停止を比較する。
  `process_loop_session_lifecycle_test.py`の停止・再開の合成プロセス試験も参照する。
  稼働中のprocess-loopのargvにはresume指定が無い通常起動を観測した。
  実稼働中の統括を停止する実験と自然発生する利用上限からの復帰は実施していない
- 現行版の巡回の終了前検収は`agent-toolkit/skills/atk-mode/playbooks/process-wi-all-repos-results.md`「結果と終了」、CLIの起動・終了と再開予約は`_atk/wi/process_loop.py`の`_run_loop`で確かめる

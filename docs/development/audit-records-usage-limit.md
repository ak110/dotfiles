# 監査記録: 利用上限と再開

[監査記録の索引](audit-records.md)に収録対象と記録の書式を示す。

## agent-toolkit/skills/user-confirmation-and-report/references/usage-limit.md：利用上限後の再開：2026年10月14日

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

## docs/development/design-hooks-compaction-resume.md：利用上限後のメインの再開予約：2026年10月14日

- 観測日: 2026年10月14日（起草時の観測報告。公式資料は2026年10月16日に再確認）
- 観測した版数: Claude Code 2.1.299
- 再検証手段: [mods API](https://code.claude.com/docs/en/plugins/mods/api)と[reference](https://code.claude.com/docs/en/plugins/mods/reference)でusageのrateLimits、clock.afterとprompt.submitを確認し、機能別modのテストで偽の使用率・時計・turnを与える。
  StopFailureのrate_limit、猶予後のturn完了、予定時刻不明、既再開、委譲先除外と表示回収を分ける。
  実上限時のpercentUsed、猶予後の打切り時のStopFailure発火は起草時も未観測であり、予約の永続化は検証範囲に含めない

## docs/development/design-agents-runtime-handoff-resume.md：Codexの利用上限記録と再開：2026年10月14日

- 観測日: 2026年10月14日（起草時の観測報告に基づく記録）
- 観測した版数: Codex CLI 0.162.1、公式ソースのタグrust-v0.162.1
- 再検証手段: ロールアウト記録のtask_complete.error.codex_error_infoがusage_limit_exceededであるturnと、直前token_count.rate_limitsのused_percent・resets_atを対応付ける。
  [turn.rs](https://github.com/openai/codex/blob/rust-v0.162.1/codex-rs/core/src/session/turn.rs)の失敗時の終了とStop hookを呼ぶ条件、[goalのextension](https://github.com/openai/codex/blob/rust-v0.162.1/codex-rs/ext/goal/src/extension.rs)の上限停止を確認する。
  TUIのthread_goal_actions.rsでは再開確認を調べる。
  偽のrollout・codex・時計でrun-skillとprocess-loopの再開を検証し、最上位TUIは解除後の実機観測へ分ける。
  実上限到達からの再開と、即時拒否turnのtoken_countの有無は今回再現していない

## docs/development/design-agents-runtime-handoff-resume.md：最上位のCodex TUIの解除後の再開：2026年10月24日

- 観測日: 2026年10月24日
- 観測した版数: Codex CLI 0.164.0（共有app-server daemon 0.164.0）、agent-toolkit 2.225.1
- 観測の結果: 引数なしの`codex`を専用ソケットのtmuxで起動し、1回のturnの後に`codex queue --thread <thread-id> --message <指示>`を実行すると、終了0で指示がturnを始め、TUIの画面に発話と応答が表示された。
  TUIのagents_serverのMCPプロセスの親はdaemonで、環境に`CODEX_THREAD_ID`は無かった。
  `/quit`でTUIを終了した5秒後と65秒後に同じMCPプロセスが残り、終了後に積んだ指示はTUIの無いままturnを開始・完了した
- 再検証手段: 隔離した作業ディレクトリで`tmux -L <専用名> new-session -d -c <作業ディレクトリ> codex`を起動し、`tmux capture-pane`で画面を取得する。
  turnの後に`ps -o ppid`と`/proc/<pid>/environ`の変数名でMCPプロセスの親と`CODEX_THREAD_ID`の有無を確かめ、TUIの前後で`codex queue`を実行してrolloutの`task_started`・`task_complete`を比べる。
  購読者の判別手段は`codex app-server generate-json-schema --out <ディレクトリ>`のClientRequestで確認する。
  実上限からの解除時刻での再開は観測していない

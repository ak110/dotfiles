# セッション状態ファイルとフラグ：Function hooksの終了要求：利用上限による中断の再開予約

Function hooks用ファイルの配置とSessionEnd・clear・期限切れの回収は`docs/development/internals-session-state-function-hooks.md`にある。

## 利用上限による中断の再開予約

`rate-limit-{session_id}.{prompt_id}.txt`は`docs/development/internals-session-state-function-hooks.md`で定めるFunction hooks用ディレクトリに配置する。
このファイルは、StopFailureコマンドhookから`turn.complete`へ利用上限（レート制限）の発生を通知するために使用する。
この処理はメインエージェントのClaude Codeのみを対象とし、委譲先、`agent_id`が付与された入力、およびCodexは除外する。

- 書き込み元: StopFailureコマンドhookが、`error`として`rate_limit`を受け取った際に`requested`を書き込む
- 消費元: 通常のClaude会話は`hooks/usage_limit_resume.ts`の`turn.complete`が`requested`を`consumed`に変え、既存の再開予約処理へ接続する。
  process-loopの同じ会話IDに対応する管理印、およびrun-skillの実行IDがある会話では起動側が再開を担当するため、modによる予約を省く。
  process-loopの統括は、起動時に明示した会話IDかpickerで確定した会話IDが一致するものに限る。
  起動元はプロンプトIDを含まない`rate-limit-{session_id}.txt`の`requested`を読む。
  旧CLIの終了を確認し、要求ファイルを削除して同じIDの接続を選び直す。
  run-skillはCLIのstream-jsonから上限を観測する
- 初期化と生存期間: 初期状態ではファイルを作成せず、`requested`の記録だけを予約実行の根拠とする。
  通常のSessionEndではファイルを保持し、`clear`や期限切れ回収時の扱いは`docs/development/internals-session-state-function-hooks.md`で定めるFunction hooks用ファイルと同様とする

## サーバー側の一時的なAPIエラーによる中断の再開予約

同じディレクトリの`server-error-{session_id}.{prompt_id}.txt`はメインの`StopFailure`で`error`が`server_error`のときだけ`requested`になる。
通常のClaude会話では`turn.start`でメインターンのプロンプトIDを取得する。
`turn.complete`は同じプロンプトIDの要求だけを消費する。
利用上限でもサーバーエラーでも、旧ターンの遅着は新しいターンで消費しない。
プロンプトIDがない場合は対話向けの要求を作らず、使用率に基づく利用上限の判定だけを保つ。
利用上限の条件も満たす場合は、両要求を消費して利用上限の再開だけを予約する。
process-loopの統括にはこの要求を作らず、run-skillの起動側も使わない。
統括が起動した別の会話では、統括と同じ環境印を継承していても通常の対話と同じファイルを読み書きする。
ファイルの回収条件は利用上限の記録ファイルと同じである。

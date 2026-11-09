# Codexでの委譲方法：agents_serverの二層待機

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 委譲先と起動手段を選ぶ前 | `runtime-routing.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/runtime-routing.md` |
| 委譲先と起動手段を選ぶ前 | `process-model-settings.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/process-model-settings.md` |
| 同一担当の継続・交代を判断する前 | `continuation.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/continuation.md` |
| 可用性の障害を確認して回復する前 | `fallback-launch.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/fallback-launch.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `waiting-and-monitoring-responses.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/waiting-and-monitoring-responses.md` |

## agents_serverの二層待機

1. `functions.exec`の内部で`agents_server`を起動し、`PostToolUse`フックがMCPの応答を直接観測できない環境では、起動応答に含まれる`root_session_id`を保持し、`atk agents wait --root-session-id <値>`へ渡す。
   CLIは状態ディレクトリの実在性と、確認済みのルートセッションとの一致を検証する。
   外側セルの`cell_id`または内側コマンドの`session_id`のいずれかが未回収である場合は、`share/rules/rules-common.codex.md`「Codex固有の入出力」に定める手段を用いて同一の実行結果を回収する。
   独立作業と受信済み通知の処理が尽きた長い終了待ちでは、同節の120,000msの回収時間を目安とする。
   外側のpragma・`functions.wait`と、session ID受領後の空入力`write_stdin`に適用し、`exec_command`起動時の上限30,000ms、待機CLI自身のtimeoutと区別する。
   制御が戻るたびに追加入力と通知を処理し、短い処理や対話へ一律に適用しない。
   外側セルや`exec_command`におけるyieldは同一実行の継続区間として扱う。
   代替起動、中断、巻き取りは`codex-runtime-continuation.md`「後続操作の共通先行条件」を満たした場合に限り実行する。

2. 同一実行を回収するため、`functions.exec`から`atk agents wait`を起動する場合も、`share/rules/rules-common.codex.md`「Codex固有の入出力」に定めるコマンド回収契約に従い、外側セルの`cell_id`を`functions.wait`へ渡す。
   待機CLIへタスク固有timeoutを渡さず、CLI自身が待機上限に達したときに対象が未終了なら、その結果を確かめてから新しい待機コマンドを実行する。
   逐次待機やロック競合によって遅れて開始された後発待機の回収手順は、`waiting-and-monitoring-responses.md`「`atk agents wait`の応答の扱い」に従う。
   監査記録は`docs/development/audit-records.md`の「agent-toolkit/share/rules-common.codex.md：Codex固有の入出力：2026年10月19日」に記載されている。

   委譲先の成果物側のみを補助観測する場合は、作業ツリーの状態を`git -C <絶対パス> status --porcelain=v1`と`git -C <絶対パス> rev-parse --short HEAD`で観測する。
   成果物ファイルは`wc -l -- <絶対パス>`および`stat -L -c %Y -- <絶対パス>`（macOSでは`stat -L -f %m -- <絶対パス>`）で観測する。
   セッション自体の稼働確認には、起動手段が提供する状態確認機能を使用する。

## 本文参照の取得条件

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| Codexでの委譲方法：後続操作の共通先行条件の基準を適用する前 | `codex-runtime-continuation.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/codex-runtime-continuation.md` |

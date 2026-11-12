# Codexでの委譲方法：agents_serverの二層待機

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 |
| --- | --- |
| 同一担当の継続・交代を判断する前 | `continuation.md` |

## agents_serverの二層待機

1. `functions.exec`の内部で`agents_server`を起動し、`PostToolUse`フックがMCPの応答を直接観測できない環境では、起動応答に含まれる`root_session_id`を保持し、`atk agents wait --root-session-id <値>`へ渡す。
   CLIは状態ディレクトリの実在性と、確認済みのルートセッションとの一致を検証する。
   外側セルの`cell_id`または内側コマンドの`session_id`のいずれかが未回収である場合は、`share/rules/rules-common.codex.md`「Codex固有の入出力」に定める手段を用いて同一の実行結果を回収する。
   終了結果だけを待つ場面では、以下の例のように外側と内側を合わせて長めに回収する。
   先行できる独立作業や受信済み通知がある場合は、その処理へ戻るため早くyieldしてよい。
   待機状況の報告だけの場合は、終了結果だけを待つ例を使う。
   短いコマンドと追加入力を送る対話は、必要な応答時間で回収する。
   外側セルや`exec_command`におけるyieldは同一実行の継続区間として扱う。
   代替起動、中断、巻き取りは`codex-runtime-continuation.md`「後続操作の共通先行条件」を満たした場合に限り実行する。

2. 同一実行を回収するため、`functions.exec`から`atk agents wait`を起動する場合も、`share/rules/rules-common.codex.md`「Codex固有の入出力」に定めるコマンド回収契約に従い、外側セルの`cell_id`を`functions.wait`へ渡す。
   待機CLIへタスク固有timeoutを渡さず、CLI自身が待機上限に達したときに対象が未終了なら、その結果を確かめてから新しい待機コマンドを実行する。
   逐次待機やロック競合によって遅れて開始された後発待機の回収手順は、`waiting-and-monitoring-responses.md`「`atk agents wait`の応答の扱い」に従う。
   監査記録は`docs/development/audit-records-codex.md`の「agent-toolkit/share/rules-common.codex.md：Codex固有の入出力：2026年10月19日」に記載されている。

   次は終了結果だけを待つ例である。
   `/absolute/path/to/atk`は確認済みCLIの絶対パス、`ROOT_SESSION_ID`は起動応答で保持した値、`CELL_ID`と`SESSION_ID`は各応答の識別子へ置き換える。
   `SESSION_ID`は整数として渡す。

   `functions.exec`で1回起動する。

   ```javascript
   // @exec: {"yield_time_ms": 120000}
   text(await tools.exec_command({
     cmd: "/absolute/path/to/atk agents wait --root-session-id ROOT_SESSION_ID",
     yield_time_ms: 30000
   }));
   ```

   `Script running with cell ID ...`が返った場合だけ、同じ外側セルを`functions.wait`で回収する。
   内側60000msの待機を外側1000msで包むと、外側の短周期回収が続く。

   ```json
   {"cell_id":"CELL_ID","yield_time_ms":120000}
   ```

   内側の結果に`exit_code`が無く`session_id`があれば、`functions.exec`から同じコマンドを回収する。

   ```javascript
   // @exec: {"yield_time_ms": 120000}
   text(await tools.write_stdin({session_id: SESSION_ID, chars: "", yield_time_ms: 120000}));
   ```

   このセルがearly yieldした場合も、返された同じ`cell_id`を前の例の`functions.wait`へ渡す。
   120000msは受理を確認した回収時間の目安であり、最大値・固定sleep・実行期限を表さない。
   `exec_command`の初回は公開上限30000ms以内とし、120000を待機CLI自身のtimeoutへ転記しない。
   先に終了すればその結果を回収し、制御が戻るたびに通知と追加入力を処理する。
   CIの終了確認など別の長いコマンドでも、起動コマンドだけをその正規手段へ替えて同じ二層で回収する。
   観測条件と再検証手順は、`docs/development/audit-records-codex.md`の「agent-toolkit/share/rules-common.codex.md：Codex固有の入出力：2026年11月5日」にある。

   委譲先の成果物側のみを補助観測する場合は、作業ツリーの状態を`git -C <絶対パス> status --porcelain=v1`と`git -C <絶対パス> rev-parse --short HEAD`で観測する。
   成果物ファイルは`wc -l -- <絶対パス>`および`stat -L -c %Y -- <絶対パス>`（macOSでは`stat -L -f %m -- <絶対パス>`）で観測する。
   セッション自体の稼働確認には、起動手段が提供する状態確認機能を使用する。

## 本文参照の取得条件

| 時点または条件 | 全文読む資料 |
| --- | --- |
| Codexでの委譲方法：後続操作の共通先行条件の基準を適用する前 | `codex-runtime-continuation.md` |

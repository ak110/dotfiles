# コマンド時間と退避出力の観測の設計記録

[設計記録の索引](design.md)から参照する、コマンド時間と退避出力の観測の記録である。

## PostToolUseの個別設計

### コマンド全体の時間観測

既存PreToolUseで元実行の開始を記録し、PostToolUse/FailureとClaudeの背景完了通知から同じ実行を完了させる。
`command_timing`はsession・呼出主体・tool_use_idと、起動応答から得た背景task-idだけを対応付ける。
Claudeの全体実行時間`duration_ms`を優先し、不足時は権限待ちや通知配送の遅延を含み得る観測経過時間として表示する。
Codexの各pollの`wall_time_seconds`は全体区間を測らないため使わず、write_stdinによる元実行の最終Postを同じ開始記録へ接続する。
開始記録がなければ未計測とし、背景起動を完了に置き換えない。
識別子の完了状態で二重計上を防ぎ、見直し用履歴は主体・計測種別ごとの直近3件だけとする。
60秒以上の完了が3件続いた区間の初回に、各所要と合計を示して実行範囲や結果再利用の検討を促す。
短い完了、未計測、計測種別の変更で新区間とし、遮断や自動中断は加えない。
独立した収集サーバー、コマンド本文の追加履歴、全コマンドへのtimeラッパーは、既存イベントと状態で目的を満たせるため採らない。

### 退避したシェル出力の抜粋の置き換え

Claude CodeのBashとPowerShellは、出力が上限（`bashOutputMaxChars`、指定しない場合30,000。比較はUTF-8のバイト数）を超えると全量を保存先へ書き、モデルへ渡すツール結果を`<persisted-output>`と出力の先頭約2KBの抜粋へ置き換える。
抜粋は取得結果の全体と同じ位置と形で届くため、2026年10月6日のセッションでは、メインがエージェント向け文書を数件まとめて`cat`した結果の抜粋だけを読み、保存先を読まずに委譲を起動した。
`02-agent-operations.md`の`Read`の箇条は残りを読むよう定めていたが、条件の主語が`Read`であり、Bashで読んだ結果へ当てはめる想起だけに任せていた。

そこで`posttooluse.py`が、`tool_response`に空でない文字列の`persistedOutputPath`を持つBashとPowerShellの結果へ`hookSpecificOutput.updatedToolOutput`を返す（判定と本文は`persisted_output.py`）。
返す値は`tool_response`の写しの`stdout`だけを、退避したことと保存先の絶対パス、元の出力のバイト数および次の操作を示す本文へ置き換え、`stderr`・`persistedOutputPath`・`persistedOutputSize`などの項目を保つ。
次の操作の行では、保存先を`Read`で範囲を指定せず`allow_large: true`を付けて読み、`PARTIAL view`の部分取得かエラーが返った場合だけ残りを`offset`と`limit`で読むよう案内する。
全体を使わない場合は対象を限定して取り直す選択肢も示す。
ホストは置き換えた値を`Preview`として示すため、受け取る本文から元の出力の先頭が消え、抜粋を全体として扱う余地が無くなる。
保存先のファイルは元の全量を保持する。

Claude Code 2.1.296の`Read`は`allow_large: true`を受け付け、通常の上限（25,000トークン、全文で256KB）の代わりにコンテキストの空きに応じた量を1回で返す。
観測は`audit-records-rules-operations.md`「agent-toolkit/rules/02-agent-operations.md：出力の保持と観測：2026年10月12日」にある。
保存先の読取をこの`Read`へ移し、通常上限に合わせた分割を案内しない。
一方、未読の通知は保持する。
`Read`の上限の緩和は、Bash・PowerShellの退避と、届いた抜粋を全量と誤認することのどちらも防がないためである。
退避の有無はホストが付ける`persistedOutputPath`で判定し、上限値をhook側で再実装しない。
PowerShellもBashと同じ出力スキーマと上限を持つため`hooks.json`の`matcher`へ加えた。
`updatedToolOutput`を選んだのは、本体の出力スキーマがこの項目を「Replaces the tool output before it is sent to the model」と説明し、`updatedMCPToolOutput`より全ツールへ働く項目として案内しているためである。
既存のPostToolUseの起動に相乗りし、新しいイベントの登録、セッション状態および起動を加えない。
同じ呼び出しで他の分岐が`additionalContext`を返す場合は、1つのJSONへ両方を入れる。
transcriptの`toolUseResult.stdout`も置き換えた本文になるため、`session_review_evidence.py`は`persistedOutputPath`が実在するファイルを指せばその内容を検索と警告の抽出の実体とする。
CodexのPostToolUseは`persistedOutputPath`を持たず（出力は切り詰めて返る）、置き換えは発動しない。

あわせて`share/claude_settings_json_managed.json`へ`bashOutputMaxChars`を配布し、エージェント向け文書の通常の取得が退避されないようにした。
値の導出と観測は`operations-claude-settings.md`「Claude CodeのBash出力の上限」にある。
62,000バイトを超える出力は退避され、この置き換えの対象となる。

却下した代替案は次のとおりである。

| 案 | 採らない理由 |
| --- | --- |
| 規範の条文だけを改める | 該当条文が文脈にある状態で、エージェント向け文書をまとめて読んだ取得の未読が8件起きていた。条文の想起だけを前提とする構造が残る |
| PostToolUseでMCPツールの退避も置き換える | MCPツールのPostToolUseの`tool_response`は退避の有無を示す項目を持たない元の出力であり、判定にホストの上限の再実装を要する。MCPの退避は集計期間に1件で、規範の書き直しで覆う |
| PostToolBatchで`<persisted-output>`を検出して通知する | 全ツールを覆えるが、全ての呼び出しの組ごとにhookの起動（約0.35秒）を加える。MCPの退避は集計期間に1件だった |
| 次のツール呼び出しで保存先を読んだかを判定して警告・遮断する | 置き換えの後は全体と誤認される抜粋が残らないため防ぐ誤りが無く、件数や対象を限定して取り直す正当な操作を誤検出する |
| `large_reads.py`の全文取得の判定をClaude Codeへ広げる | 未読8件のうち`cat`へ複数ファイルを渡す単純な形の3件しか静的に判定できない |
| Bashの退避を失敗として返す | 同じ版の設定スキーマにその設定が無い。置き換えでツール結果そのものが未読を示す形にする |
| 上限を最大の128,000にする | 1回のツール出力が約41,000トークンとなり、Codexへ配布する上限（20,000トークン）を超える。退避した全量は`Read`の`allow_large: true`で読めるため、上限を上げて退避を避ける利点も小さい |

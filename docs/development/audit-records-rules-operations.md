# 監査記録: 共通規範とコマンド運用

[監査記録の索引](audit-records.md)に収録対象と記録の書式を示す。

## agent-toolkit/rules/01-agent.md：方針が衝突する場合の優先順位：2026年9月25日

2026年9月25日、Claude Code 2.1.281で新しいセッションを起動し、`--include-hook-events --output-format stream-json`でhook応答を保存した。
SessionStartの`additionalContext`は`<agent-toolkit-auto-inserted source="agent-toolkit/rules_context" kind="notice">`で始まった。
存在しないパスを指定したBash検索に対するPreToolUseの`additionalContext`は`<agent-toolkit-auto-inserted source="agent-toolkit/pretooluse" kind="warn">`で始まった。
両応答の`exit_code`は0だった。
検証用セッションはhook応答を得た後に中断したため、セッション全体の完了結果はこの観測の根拠に含めない。
再検証するときは、同版以降で`--include-hook-events`を付けて新しいセッションを起動する。
SessionStartと、`uv.lock`を`Write`で書き込むPreToolUseの`hook_response.output`を読む（存在しないパスへの警告は2026年9月26日に撤去した）。
各応答の外側境界にある`source`と`kind`が上記と一致するか確かめる。

## agent-toolkit/rules/02-agent-operations.md：ツール・コマンド運用：2026年9月5日

2026年9月5日、GNU bash 5.2.21で、外側のbashの二重引用符へ置いたドル記号付きの変数は外側で展開され、単一引用符で囲んだ値は`sh -c`を2段重ねた実行で引用符を失うことを実際に動かして確かめた。
再検証は外側のbashの二重引用符の内側へドル記号と単一引用符を含む文字列を置き、`sh -c`を2段重ねて出力を確認する。

## agent-toolkit/rules/02-agent-operations.md：ツール・コマンド運用：2026年9月24日

2026年9月24日、Claude Code 2.1.280のBashツールでは、実在するパスと存在しないパスへの`test -e`がともに`(Bash completed with no output)`を返した。
両呼び出しの一次記録はリポジトリ外のセッション記録にあり、本節は観測内容と再検証手段を保持する。
bash 5.2.15で`test -e /dev/null; echo "test_e_rc=$?"`は標準出力へ`test_e_rc=0`を返し、存在しない`/dev/__agent_toolkit_audit_absent__`では`test_e_rc=1`を返した。
再検証はClaude CodeのBashツールで同じ2種類のパスへ`test -e`と表示付きの起動形をそれぞれ渡し、ツール表示と標準出力の値を対比する。

## agent-toolkit/rules/02-agent-operations.md：ツール・コマンド運用：2026年9月25日

2026年9月25日、Claude Code 2.1.282で`agent-toolkit/skills/wi-standards/SKILL.md`の309行・52,347バイトを`Read`の`offset=1, limit=309`で取得すると、末尾まで届いた。
同じファイルを`cat <絶対パス> | cat`で取得すると、Bashは表示上限を超えた全量をセッション内のファイルへ保存した。
保存物は309行・52,347バイトで原本と一致した。
Claude Codeの[tools仕様](https://code.claude.com/docs/en/tools.md)の「Read tool behavior」は、上限超過時に`PARTIAL view`と先頭ページを返し、`offset`と`limit`で続きを取得する方式を説明する。
同仕様の「Bash」は、表示上限を超えた結果をセッション内のファイルへ保存してパスを返す。
2026年9月21日のCodexでは、複数文書の全文取得を1つの実行セルへ集約した結果、15,105トークンで出力が切り詰められ、個別再取得を要した。
この観測時のCodexのホスト版番号は記録されていない。
再検証では両ホストへ同一の大容量エージェント向け文書を与えて全文読取とBashの単純全文読取を実行する。
hook通知、ホストの部分取得通知、保存物の末尾および容量を比較する。

## agent-toolkit/rules/02-agent-operations.md：出力の保持と観測：2026年10月12日

2026年10月12日、`claude --version`は`2.1.296 (Claude Code)`を返し、同版の本体の実行ファイルは`allow_large`の文字列を含んでいた。
Claude Codeの[tools仕様](https://code.claude.com/docs/en/tools)の「Large files」は次を説明する。
`Read`の1回の返却は通常25,000トークン（`CLAUDE_CODE_FILE_READ_MAX_OUTPUT_TOKENS`で変更可能）までで、256KBを超えるファイルの全文取得は拒否される。
v2.1.296以降は`allow_large: true`を指定すると、ファイル全体か長い行範囲をセッションのコンテキストの空きに応じた量で1回に読める。
画像、PDF、notebookは従来の上限を保つ。
上限を超えた場合、全文取得は先頭ページと`PARTIAL view`の通知を返し、256KB超の全文取得と上限超過の範囲指定はエラーを返す。
どちらも`offset`と`limit`での部分取得を案内する。
同日のセッションで、通常上限内のファイルへの`allow_large: true`付きの`Read`は全文を返した。
通常上限を超えるファイルでの返却は観測していない。
再検証では`claude --version`で版を確かめ、tools仕様の「Large files」を読む。
通常上限を超える追跡ファイル（`uv.lock`など）を`allow_large: true`付きと無しの`Read`でそれぞれ取得し、全文の到達、`PARTIAL view`の通知、エラーの有無を比べる。

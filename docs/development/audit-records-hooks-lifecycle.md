# 監査記録: フックの起動と終了

[監査記録の索引](audit-records.md)に収録対象と記録の書式を示す。

## agent-toolkit/agent_toolkit/_hooks/stop.py：未配送通知を次ターンへ返す：2026年10月10日

Claude Code 2.1.296の非対話CLIで、goalなし・ありの専用sessionを1件ずつ起動した。
対象版は基準commit `fb5cbb72380af87aa26c302816b0c87efdb17004`へ本変更の未commit差分を加えた作業ツリーである。
配布するStopの起動コマンドを一時settingsへ登録し、実際のstdin・応答・終了コードを保存した。
各sessionはBashで固定文字列を1回だけ背景出力し、Read前に待機を表明した。
両方でStopは終了0の`continue: false`を返した後、同じsessionがtask-notificationによる次ターンで再開した。
Readは背景の出力ファイルの全量と`[exited with code 0]`を取得し、最終結果は`terminal_reason: completed`だった。
goalありでは実際の`Goal set`、待機時の未充足評価、結果受領後の正常終了を保存した。
追加のgoal観測では`--debug-file`を指定した。
同じ条件の評価は待機時に`ok: false`、通知からの再開後に`ok: true`となり、その後にsessionのStop評価hookが除去された。
この追加観測の実行記録は`atk-command-t1dania1/record.json`、詳細索引は`session-4bef2h87/goal-evaluation-observation.json`にある。
背景コマンドの再発行と追加のユーザー入力は無かった。

証拠索引は管理対象一時領域の`session-4bef2h87/delivery-observations.json`で、各条件のargv・session・両出力・StopログとCLI終了を保持する。
外側の実行記録は`atk-command-pez6xwtn/record.json`へ保存した。
再検証は`claude --version`と`--help`を確認し、専用sessionで同じBash・Readの順序を指示する。
起動には次の公開引数を使う。

```sh
claude -p --session-id <専用UUID> --setting-sources '' --strict-mcp-config \
  --settings <Stop登録JSON> --tools Bash,Read --allowedTools 'Bash(printf *)' Read \
  --output-format stream-json --include-hook-events --verbose <指示本文>
```

goalありは公開された`/goal`を先頭に置き、設定・評価・同一sessionの再開を併せて確認する。
Stopの応答だけやホストのtask_notificationだけを、モデルによる結果受領の代用にしない。
他のblockとの合成は`stop_test.py`が扱い、本実機観測はblockなしのBash結果に限る。
上流の修正やIssue投稿は行わない。
上流改善後に同条件で警告による継続が通知配送を妨げなくなったことを確認して、回避の撤去を判断する。

## agent-toolkit/agent_toolkit/_hooks/termination_evidence.py：終了工程の証拠のStop判定：2026年10月3日

Stopで報告の不足を判定する変更（2026年10月4日「fix: 残る報告工程と未発話の報告で停止しないようStopで検出する」、`4862700e1`）の要求を起草した時点で、両ホストの公式Hooks仕様のStopとPostToolUseを確認した。
資料はCodexが<https://learn.chatgpt.com/docs/hooks>、Claude Codeが<https://code.claude.com/docs/en/hooks>である。
両ホストのStopは`last_assistant_message`を供給し、`decision: "block"`と`reason`で同じターンを継続する。
CodexのStopは`hookSpecificOutput`を受理せず、CodexのPostToolUseのBashの`tool_response`は終了コードを含まない出力文字列である（`claude-hooks.md`の既存記録と同じ）。
実装時の作業ホストはcodex-cli 0.160.0とClaude Code 2.1.288である。
確認した範囲は判定器の契約テストまでである。
対象は`termination_evidence_test.py`と`completion_report_delivery_advisor_test.py`の判定である。
加えて`output_contract_test.py`がCodex Stopの出力を、`sync_codex_plugin_manifests_test.py`が生成を確かめた。
ホスト本体のStopの発火と継続、Codexの未信頼設定や無効化されたhookでの挙動は実機で試験していない。
再検証は両ホストの公式Hooks仕様のStop・PostToolUseの入力と出力を取得し、`last_assistant_message`、Codexの`tool_response`の形とStopの出力契約を比べる。
変わった場合は`termination_evidence.py`の可視本文と応答の読取、`output_contract.py`のCodex Stopの契約を改める。

## agent-toolkit/agent_toolkit/_hooks/user_prompt_submit.py：UserPromptSubmitの出所欄：2026年9月17日

2026年9月17日に確認した。
Claude Code 2.1.274の実行ファイルは、UserPromptSubmitの入力スキーマへ`source`を宣言し、値を`user`、`sdk`、`system`、`loop_wakeup`、`schedule_wakeup`、`poll_event`の6種とする。
同スキーマの説明は`schedule_wakeup`を`scheduled-task fire (CronCreate/routine)`と定める。
`system`は`other machine-injected turns (peer/channel messages, task notifications, auto-continuation)`と定める。
説明の末尾へは`Payloads may omit it while the field rolls out.`と記す。
一方、同じ実行ファイルでUserPromptSubmitの入力を組み立てる2箇所は、いずれも`prompt`の直後に`...!1`を持つ。
この部分では代入が畳み込まれている。
同じ実行ファイルのSessionStartの入力組み立てには`source:n`があるため、UserPromptSubmitの`prompt`直後にある`...!1`は出所欄の欠落を示すと判定した。
2.1.272と2.1.273も同じ形である。
確認の範囲は、この3版が出所欄を配送しないこととする。
配送を開始する版と時期は確認していない。
この観測により、出所欄だけを判定入力とする案は現行版で成立しないため、機械注入ターンの判定を4系統で構成した。
再検証は対象版の実行ファイルから`hook_event_name:"UserPromptSubmit"`の前後の文字列を取得する。
`prompt`直後に`source`の代入が現れるか、`hook_event_name:"SessionStart"`の同じ箇所と対にして確認する。

## agent-toolkit/hooks/hooks.json：SessionEndの非同期化：2026年9月24日

Claude Code 2.1.281を`--plugin-dir`で作業ツリーのプラグインから2回起動した。
debugログはどちらも`SessionEnd:other`を非同期hookとして登録し、予算を`600000ms`と記録した。
hookの完了状態は2回とも0で、事前に各セッションIDへ登録した管理対象一時領域は終了後に実在しなかった。
標準エラーは空で、debugログにも`Hook cancelled`は現れなかった。
CLI本体は2回とも終了コード143で、経過時間は11.60秒と16.45秒だったため、全プラグイン構成での通常応答の完了は未確認である。
非同期hookの実行時間もdebugログからは分離できない。

同日、`hooks.json`のSessionEnd登録と同じコマンド・`async: true`だけを一時設定へ写し、`--setting-sources ''`と`--settings`で指定した`claude -p`を実行した。
CLIは終了コード0で、結果JSONは`terminal_reason: completed`、`result: OK`だった。
標準エラーは空で、debugログに`Hook cancelled`はなく、`SessionEnd:other`を非同期hookとして登録した記録、`600000ms`の予算および完了状態0があった。
同一`session_id`で事前登録した管理対象一時領域はCLI終了後に実在しなかった。
この検収はSessionEnd単独構成の正常終了を示す。
全プラグイン構成を用いた同日の追加試行も終了コード143だったため、その正常終了は引き続き未確認である。
再検証では`claude -p`へ同じSessionEnd設定、専用の`--session-id`、`--debug-file`を指定し、CLIの終了コードと結果JSON、標準エラー、debugログの登録・完了状態・予算・`Hook cancelled`の有無を確認する。
2026年9月26日以降のSessionEnd hookはセッション単位の領域を削除しないため、同IDで作成した領域の終了後の不在はhookの完了の観測に使わない。

2026年9月24日、Claude Code 2.1.281を作業ツリーのagent-toolkitを`--plugin-dir`で読み込む全プラグイン構成で再検証した。
`claude -p`に専用の`--session-id`、`--debug-file`、`--output-format json`を指定し、標準入力を`/dev/null`へ接続した。
外側に時間制限は設けなかった。

先の終了コード143は、検証元から継承した`AGENT_TOOLKIT_PROCESS_LOOP_SESSION=1`と`DOTFILES_AUTONOMOUS_EXIT_REQUIRED=1`による。
再現試行の`strace`はagent-toolkitのStop hookの子プロセスからCLI本体への`SIGTERM`送信を記録した。
debugログにも無進捗判定と常駐ループへの中断要求があり、SessionEnd hook自体は完了状態0だった。

常駐ループ用の環境変数を外し、同じ全プラグイン構成でCLIを起動すると、7秒で終了コード0となった。
結果JSONは`terminal_reason: completed`、`result: OK`、`is_error: false`を返した。
標準エラーは空で、debugログは`SessionEnd:other`を非同期hookとして登録し、予算`600000ms`と完了状態0を記録した。
`Hook cancelled`はdebugログと標準エラーの双方に無かった。
同じ`session_id`で事前登録した管理対象一時領域は終了後に実在せず、`atk managed-temp list --prefix sessionend-probe`も該当0件を返した。
再検証では常駐ループ用の環境変数を検証用CLIへ継承させない。

## docs/development/design-hooks-notices.md：通知の整形と反復検出：2026年10月13日

2026年10月13日、Claude Code 2.1.296の環境で公式の[Hooks reference](https://code.claude.com/docs/en/hooks)を取得した。
「Common input fields」は`session_id`を現在のセッション識別子、`transcript_path`を会話のJSONへのパスとし、transcriptは非同期に書かれるため現在のturnの最新メッセージを含まない場合があると記す。
「SessionStart」はmatcherを`startup`・`resume`（`--resume`、`--continue`、`/resume`）・`clear`・`compact`・`fork`とし、v2.1.214より前はforkしたセッションが`resume`を報告したと記す。
`rules_context`はこの入力の`session_id`と`transcript_path`をClaude Codeのメインへの通知に使う。
`agent-toolkit/agent_toolkit/_hooks/rules_context_test.py`が、会話ごと・切替ごとの入力と出力の対応と、managed-tempの作成失敗時の通知を確かめる。

再検証は同じ資料の2節を取得して項目名とmatcherの値を比べ、制御したSessionStart入力をhook handlerへ渡して`additionalContext`の値を確かめる。
項目名か値が変わった場合は`rules_context.py`の通知、`agent-toolkit/share/rules-main.claude-code.md`「自身の会話の記録」と同テストを改める。

## docs/development/design-hooks-session-state.md：通知の整形と反復検出：2026年10月20日

2026年10月20日、Claude Code 2.1.304で、開始時と別のディレクトリで再開すると、SessionStart入力の`transcript_path`が再開時のディレクトリに対応する実在しないパスを示した。
同じ会話のPreToolUse入力63件は、開始時のworktree側にある実在する記録を示した。
今回の文書追随では元ホストで再現していない。
再検証では専用会話を開始時と別のディレクトリで再開し、SessionStartと同じ会話のPreToolUseの`transcript_path`およびファイルの実在を比べる。
通知は同じsession IDの完全一致で1件へ解決し、0件では新規会話の未作成を許容して入力値を保ち、複数件と探索失敗ではID指定による取得を案内する。
一次記録はリポジトリ外の作業記録にある。

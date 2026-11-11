# 監査記録: agents_server

[監査記録の索引](audit-records.md)に収録対象と記録の書式を示す。

## agent-toolkit/agent_toolkit/_agents_server/claude.py：結果の保留とturn状態の報告：2026年10月4日

2026年10月4日、Claude Code 2.1.289とClaude Agent SDK 0.2.163で、turn状態の報告を受け取れることを確かめた。
`ClaudeAgentOptions(env={"CLAUDE_CODE_EMIT_SESSION_STATE_EVENTS": "1"})`を与えたsessionでは、`ClaudeSDKClient.receive_messages()`へ`SystemMessage`が届く。
その`subtype`は`session_state_changed`、`data["state"]`は`running`・`idle`・`requires_action`のいずれかである。
この変数が無い場合、SDKは`CLAUDE_CODE_SDK_READS_SESSION_STATE=1`を設定して報告を呼び出し側から除く。
haikuの委譲先に`sleep 2`を背景実行させて直ちにturnを終えさせ、Stop hookの待機時間を変えて到着順を比べた。
待機0秒では次の順に届いた。
`ResultMessage`、`idle`、`task_notification`、`running`、再開turnの`ResultMessage`（`origin.kind`が`task-notification`）、`idle`である。
待機5秒では`task_notification`（Stop hookの実行中）、`ResultMessage`、再開turnの`ResultMessage`、`idle`の順に届き、最初の結果の後に`idle`は届かなかった。
どちらも再開turnが実行された。
CLIのスキーマ記述は`idle`を、保留した結果の送出と背景エージェントの待機の後に発行するturnの終了の信号と説明する。
同じ版の組で上記の環境変数を与えた委譲先へ背景実行の`sleep 2`と即時のturn終了を指示して再検証する。
Stop hookに0秒と5秒の待機を入れた2条件で、`receive_messages()`の全メッセージの種別、`subtype`、`state`、`origin`を到着順に保存して比べる。

## agent-toolkit/agent_toolkit/_agents_server/codex.py：コンパクション計測通知：2026年9月10日

2026年9月10日、Codex CLI 0.153.4の`codex app-server generate-json-schema --out <管理対象一時領域の絶対パス>`が終了コード0で生成したJSON Schemaを検証した。
`v2/ItemStartedNotification.json`は`item`・`startedAtMs`・`threadId`・`turnId`を必須とし、`startedAtMs`をitem lifecycle開始時のUnix時刻（ミリ秒）と定める。
`v2/ItemCompletedNotification.json`は`completedAtMs`・`item`・`threadId`・`turnId`を必須とし、`completedAtMs`をitem lifecycle完了時のUnix時刻（ミリ秒）と定める。
`ThreadItem`は`id`と`type`を必須とし、`type`が`contextCompaction`である`ContextCompactionThreadItem`を変種に持つ。
再検証は同じコマンドで現行版のJSON Schemaを生成する。
続いて、2つの通知の必須項目と時刻の説明、`ContextCompactionThreadItem`の必須項目を確認する。

## agent-toolkit/agent_toolkit/_agents_server/codex.py：孫sessionの待機表明と自動再開：2026年10月1日

2026年10月1日、codex-cli 0.159.3とClaude Code 2.1.286を同じホストで使い、変更後の作業ツリーで`agents_server_live_test.py::test_live_grandchild_wait_resumes_same_session`を実行した。
委譲先は`agents_server`の起動ツールで`sleep 5`を実行する孫sessionを起動し、回収前に`待機中: <孫のsession_id>`を出力してターンを終える指示を受けた。
Codexの委譲先（`codex:sol/medium`）とClaudeの委譲先（`claude:sonnet[1m]/medium`）のいずれも、待機表明の結果は呼び出し元へ配送されず、孫の終了後に同じsessionが手動の指示なしに再開した。
再開したターンは`atk agents wait`で孫の結果を回収し、呼び出し元は`AUTO_RESUME_COMPLETED`と`turn_seq`2の完了結果を受け取った。
2件とも成功し、所要時間は141秒だった。
変更前のCodex backendは、同じ待機表明を`completed`の結果として公開し、孫の識別子を`error.unobservedSessions`へ記録していた（同日の対照観測）。
再検証は`make test-agents-live`と同じpytest引数（`-m live_agents`）を指定し、呼び出し元の会話と状態ディレクトリを分けるため別の`CLAUDE_CODE_SESSION_ID`を与えて同じテストを実行する。

## agent-toolkit/agent_toolkit/_agents_server/codex_resume_state.py：会話状態の生成先：2026年10月23日

Codex担当がCLI 0.164.0の同日rolloutで、先頭`session_meta.model_provider`、providerを持たない`turn_context`、`response_item`のreasoning内`encrypted_content`を観測した。
対象はリポジトリ外のセッション記録である。
値を再掲せず、フィールドの所在と型を確認した。
互換性検体はこの形を架空のIDと暗号文へ置換している。
再確認時は当該版の実会話で生成したrolloutの`session_meta`と不透明状態を持つ応答のキーを読み、接続先選択と生成先の記録を分けて照合する。
元障害の別ホストの原記録を再現したという証拠には用いない。

## agent-toolkit/agent_toolkit/_agents_server/launch_prompts.py：委譲先のベース指示：2026年10月23日

codex-cli 0.163.0の`codex app-server generate-json-schema --out <保存先>`でschemaを生成した。
生成された`v2/ThreadStartParams.json`と`v2/ThreadResumeParams.json`は、どちらも`baseInstructions`と`developerInstructions`を受理する。
ベース指示の差替えと既存のdeveloper指示の配送を併用できる。
再検証は導入版を`codex --version`で確認し、同じ公開コマンドが生成する両schemaで行う。

Claude Agent SDK 0.2.170を使った`claude_test.py`では、通常委譲の`system_prompt`が
独自ベース本文・委譲先規範・自動再開通知・CLI情報を含む文字列であることと、軽量区分の設定の保持を確認した。
`codex_test.py`では全起動区分の開始・通常再開・復元再開に同じベース本文が入ることを確認した。
これらと`launch_prompts_test.py`、`dot_codex_config_test.py`の合計247件は成功した。
この観測は公開リクエストとSDK引数の構築を検証するもので、外部モデルによる回答内容の実測は含まない。

## agent-toolkit/agent_toolkit/_agents_server/state.py：session初期化の待機上限：2026年9月11日

2026年9月11日、`~/.claude/projects`配下の記録（Claude Code 2.1.239から2.1.268）で、`agents_server`のstart系ツールの呼び出し489件を集計した。
このうち233件は起動された子sessionの記録と対応付けられた。
各件について、呼び出しのtool_useの時刻から子sessionの記録の先頭エントリの時刻までの差を求めた。
中位値は1.34秒、90%点は7.12秒、99%点は36.76秒であり、232件が47.65秒以内に収まった。
残る1件は604.22秒であった。
同じ489件のうち7件は、tool_useから応答までの経過が1800.4秒から1800.5秒であり、ホストがMCPツール呼び出しを打ち切った回であった。
ホスト側の上限は、Claude Codeが`CLAUDE_CODE_MCP_TOOL_IDLE_TIMEOUT`が未設定の場合に1800秒を課し、
Codexが<https://learn.chatgpt.com/docs/extend/mcp?surface=cli>の`tool_timeout_sec`が未設定の場合に60秒を課す。
再検証は同じ記録へ同じ集計を適用し、tool_useの時刻と子sessionの記録の先頭エントリの時刻の差の分布と、応答の打ち切りに達した件数を対比する。
上限値はこの分布に加えて、ホストがMCPツール呼び出しを背景タスクへ移す閾値を制約に持つ。
2026年9月、Claude Codeで`agents_server`の`wait`が発行から120秒で背景タスクへ移り、完了すると結果本文がそのタスクの通知として届くことを確認した。
背景移行の閾値の再検証は`wait`を発行し、背景移行の通知を受領した後に完了通知の到達を確認する。

## docs/development/design-agents-runtime.md：MCP接続失敗の再接続：2026年10月20日

2026年10月20日、LinuxのPython 3.15でMCP再接続の契約を検証した。
対象は`agent-toolkit/agent_toolkit/_agents_server/claude_connections_test.py`の`test_mcp_reconnect_is_once_per_new_and_resumed_process`である。
managerの公開start/showとSDKクライアントのテストダブルを組み合わせ、接続失敗のキャッシュによる省略と`CONNECT_TIMEOUT`、再接続の回復成功と失敗の4条件で、新規起動と再開の各プロセスの再接続が1回であることを確認した。
未回復のサーバー名と初期エラーはstart/showのwarningsと`claude_mcp_recovery_failed`のロガー出力へ残り、回復成功時はwarningsが消える。
同じ4条件で再接続をno-opへ差し替えた対照は4件とも失敗し、正常な処理では4件とも成功した。
再検証は同テストと`test_slow_mcp_recovery_keeps_start_budget_and_updates_show`をpytestで実行し、新規・再開の呼出回数、未回復warnings、回復後のshowとログを比較する。
ログの保存先は`agent-toolkit/agent_toolkit/_agents_server/logging_config.py`と`mcp_tools_test.py`の起動・終了診断の試験で確かめる。
この観測はテストダブルによる契約検証であり、実Claude CLIと外部MCPサーバーの接続障害を再現したものではない。

## agent-toolkit/agent_toolkit/_agents_server/codex.py：待機中の独立圧縮turn：2026年11月2日

- 観測日: 2026年11月2日。
- 確認版: Codex CLI 0.166.0。
  公式App Serverの公開`thread/compact/start`は空オブジェクトを返してから、別turnのcontextCompaction項目とturn/completedを通知した。
  圧縮中の通常turn/startは`ActiveTurnNotSteerable { turn_kind: Compact }`を返した。
- 再検証: 公開generate-json-schemaでthreadIdの入力契約を確認し、読取専用の新規threadで初回応答、圧縮受付、独立turn完了、保持情報を答える通常turnの順に観測する。
  所有AppServerManagerの試行では、使用量50%と未完了の子を隔離入力として与え、圧縮中の保留・元turn ID・通常追送の待機と、圧縮終了後の結果回収を確認する。
  `wait_compaction_backend_test.py`で完了・失敗・明示拒否・取消後の後着通知を確認する。
  使用量の差し替えによる観測を、実際に文脈50%まで蓄積した試行と区別する。

# hookと実行基盤の環境変数

## 環境変数の一覧

配布物内で完結する環境変数（`AGENT_TOOLKIT_<PURPOSE>`形式）の一覧を示す。
変数名の読み書き処理について、private-notesは`agent_toolkit/_common/private_notes.py`が管理する。
委譲先を示す印と委譲元の識別子は`agent_toolkit/_common/delegated_session.py`が管理し、その他の処理はこれらが提供する定数や関数を利用する。

- `AGENT_TOOLKIT_PRIVATE_NOTES`: `atk wi`が管理するリポジトリのルートパス（未指定時は`~/private-notes/`）。
- `AGENT_TOOLKIT_STOP_GATE_DEBUG`: デバッグ出力用フラグ。
- `AGENT_TOOLKIT_HOOK_PAYLOAD_DUMP`: 受信したpayloadのダンプ出力先。
- `AGENT_TOOLKIT_RESTART_SPEC`: AWI処理の常駐実行において、次に起動するセッションの指定情報を起動側処理へ渡すための一時ファイルパス。
- `AGENT_TOOLKIT_DELEGATED_SESSION`: Claude Code（backend）の委譲先として起動したセッションであることを示す印（値`1`）。
  委譲先であるかの判定（`is_delegated`）は、この印または後述の`AGENT_TOOLKIT_OWNER_SESSION`のいずれかを保持している場合を対象とし、常駐実行の終了保証を最上位セッションに限定する判定などに使用する。
- `AGENT_TOOLKIT_OWNER_SESSION`: 委譲先が取得または作成した計画バンドルの所有元として記録する、委譲元セッションの識別子。
  `agents_server`が起動した子セッションのみが保持するため、Codex backendの委譲先を含めてメイン向けの入口の起動の案内の追加を省略する判定にも使用する。
- `AGENT_TOOLKIT_DELEGATED_LAUNCH_KIND`: `agents_server`がClaude backendおよびCodex backendの委譲先へ渡す起動区分（`delegate`、`explore`、`shell`、`write`）。
  軽量起動の判定（`is_lightweight_delegated`）がこれを参照し、操作の直前のスキルの案内（`pretooluse/operation_skills.py`）を軽量の委譲先へ出さない判定に用いる。
  Function hooks moduleは参照しない。
- `AGENT_TOOLKIT_DELEGATED_PLUGIN_ROOT`: Codex backendが開始・再開時に選んだagent-toolkitの資料rootの絶対パス。
  App Serverの子環境と内側MCPへ渡し、hookの通知が示す資料のパス、終了工程の手順書の解決、CLI案内が同じ実体を使う。
  hookは実体とplugin識別を検証し、不正な明示指定を診断する。
  無指定時はhook自身の配布物を使う。
  新しい会話への一般的な環境継承では除去し、起動側が選んだ値を設定する。
- `AGENT_TOOLKIT_PROCESS_LOOP_SESSION`: AWI処理の常駐実行が起動したセッションであることを示す印（値`1`）。
  後述の`AGENT_TOOLKIT_PROCESS_LOOP_SESSION_ID`が存在する場合は、hook入力の会話IDとの一致も確認する。
- `AGENT_TOOLKIT_PROCESS_LOOP_SESSION_ID`: process-loopがClaude会話の新規起動またはID指定再開時に子セッションへ渡す会話ID。
  hook入力の`session_id`と突き合わせ、環境変数の印を継承した入れ子の別会話を、自律終了、空転防止ガード、計画保存通知、セッション名設定、および観測ログの対象から除外する。
- `AGENT_TOOLKIT_PROCESS_LOOP_INSTRUCTION`: 常駐実行がセッション起動時に渡す追加指示の本文。
  `rules_context`が委譲先を除くメインセッション開始時のコンテキストへ配置する。
- `AGENT_TOOLKIT_SESSION_START_INPUT`: process-loop・run-skillが渡す起動元、スキル、モードと可変入力のJSON。
  SessionStartがメインへ機械生成標識付きで配送し、委譲先の環境継承とテスト環境では除去する。
  Claudeの出力上限を超える場合はmanaged-tempの原本参照を配送する。
- `AGENT_TOOLKIT_RUNTIME_DATA`: 配布ルートの外側に配置する共有依存環境の保存先。
  起動処理がパスを確定し、委譲先へも明示的に引き継ぐ。
- `AGENT_TOOLKIT_RUNTIME_ENVIRONMENT`・`AGENT_TOOLKIT_CALLER_UV_PROJECT_ENVIRONMENT`: 起動処理が選定した依存環境、および呼び出し元の`UV_PROJECT_ENVIRONMENT`を保持する内部値。
  子プロセスへ渡す環境を整える処理が、環境変数の復元後にこれらを除去する。
- `AGENT_TOOLKIT_LAUNCH_RESTORE`: 起動処理によって変更された環境変数の変更前後の値を保持する内部JSON。
  `plugin_environment`が記録し、`inherited_venv`が現在値と記録値の一致する項目のみを復元した上で記録を除去する。
  起動後に外部から変更された値は維持する。

ホストが規定する以下の環境変数は、`agent_toolkit/_common/host_homes.py`の関数のみが参照する。

- `CLAUDE_CONFIG_DIR`: 空でない絶対パスが指定されている場合はClaude Codeの設定ディレクトリとして使用し、それ以外は`~/.claude`を使用する。
  Function hooks module（`hooks/session_exit.ts`）も同一の規則で解決する。
- `CODEX_HOME`: 空でなければCodexのホームディレクトリとして使用し、空または未設定の場合は`~/.codex`を使用する。

Claude Code公式の`CLAUDE_CODE_EXPERIMENTAL_CODE_MODE`は対応する対話ターミナルの通常起動で有効にする。
Agent SDKを使う`claude_options.py`は接続環境と親設定を合成した後に`0`を指定し、通常起動の`1`をSDKへ継承しない。

会話内パネルのeffort操作は、`$.session.id()`の実会話IDを`process.run`の`CLAUDE_CODE_SESSION_ID`へ渡す。
`effort_requests.py`の既存の本人・直接親の判定を使い、対象担当のIDを呼出元として渡さない。

# Claude Code・Codex Hook実装ガイドライン

品質基準の正式な提供元は`agent-basics:writing-standards`の`references/claude-hooks.md`である。呼出元が指定した条件と読込範囲を、同名の正式な品質資料へ引き継ぐ。節指定がある場合は移設先の実見出しへ接続し、指定がない場合は従来の全文読込契約を保つ。

以下はagent-toolkitが所有する運用条件である。呼出元の条件と読込範囲に従い、ここに残る運用節を補う。

## 名指し参照の読込方法

本文の各参照の適用条件を保ち、選んだ資料の指定範囲を取得してから、その基準を使う具体的な判断へ進む。起動時の行の資料は起動本文とともに届く。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:writing-standards/references/claude-hooks.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/claude-hooks.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `claude-hooks-stop.md#「委譲先での発火」` | 判断 | `section:agent-toolkit:*/skills/writing-standards/references/claude-hooks-stop.md#「委譲先での発火」` |

## hookスクリプトの基本プロトコル

  `SessionStart`は`agents_server`の委譲先でも発火し、`SubagentStart`は`Agent`ツールのサブエージェントの起動時だけ発火する。`agent-toolkit/agent_toolkit/_hooks/rules_context.py`は前者で環境変数`AGENT_TOOLKIT_DELEGATED_SESSION`と`AGENT_TOOLKIT_OWNER_SESSION`により委譲先を判別し、メイン向け条文を除く。後者ではサブエージェント向け条文を追加する。`Stop`と`SubagentStop`の判別は`claude-hooks-stop.md`「委譲先での発火」が定める

## 環境変数の一覧

配布物完結の環境変数（`AGENT_TOOLKIT_<PURPOSE>`形式）の一覧を示す。
変数名を読み書きする処理は、private-notesを`agent_toolkit/_common/private_notes.py`、委譲先の印と委譲元の識別子を`agent_toolkit/_common/delegated_session.py`が持ち、他の処理はそれらの定数と関数を使う。

- `AGENT_TOOLKIT_PRIVATE_NOTES`: `atk wi`管理repoのroot（指定がなければ`~/private-notes/`）
- `AGENT_TOOLKIT_STOP_GATE_DEBUG`: デバッグ出力
- `AGENT_TOOLKIT_HOOK_PAYLOAD_DUMP`: 受信payloadのダンプ先
- `AGENT_TOOLKIT_RESTART_SPEC`: AWI処理の常駐実行で、次に起動するセッションの指定を起動側の処理へ渡す一時ファイルのパス
- `AGENT_TOOLKIT_DELEGATED_SESSION`: Claude Code（backend）の委譲先として起動したセッションの印（値`1`）。委譲先かの判定（`is_delegated`）はこの印か次項の識別子のどちらかを持つ場合を委譲先とし、常駐実行の終了保証を最上位セッションへ限定する判定などに使う
- `AGENT_TOOLKIT_OWNER_SESSION`: 委譲先が取得または作成した計画バンドルの所有として記録する委譲元セッションの識別子。`agents_server`が起動した子だけが持つため、Codex backendの委譲先を含めてメイン向け規範の追加を省く判定にも使う
- `AGENT_TOOLKIT_DELEGATED_LAUNCH_KIND`: `agents_server`がClaude backendとCodex backendの委譲先へ渡す起動区分（`delegate`・`explore`・`shell`・`write`）。軽量起動の判定（`is_lightweight_delegated`）が読み、操作を起動の契機とするスキルの取得を軽量委譲先へ求めない判定に使う。Function hooks moduleは読まない
- `AGENT_TOOLKIT_PROCESS_LOOP_SESSION`: AWI処理の常駐実行が起動したセッションの印（値`1`）。次項のIDがある場合は、hook入力の会話IDとの一致も確認する
- `AGENT_TOOLKIT_PROCESS_LOOP_SESSION_ID`: process-loopがClaude会話の新規起動またはID指定再開で子へ渡す会話ID。hook入力の`session_id`と比べ、環境印を継承した入れ子の別会話を自律終了、空転ガード、計画保存通知、セッション名および観測ログの対象から外す
- `AGENT_TOOLKIT_PROCESS_LOOP_INSTRUCTION`: 常駐実行がセッション起動時に渡す追加指示の本文。`rules_context`が委譲先を除くメインのセッション開始時の文脈へ置く
- `AGENT_TOOLKIT_LARGE_READ_BYTES`: CodexのBashによる全文取得を分割読取へ誘導する`pretooluse/large_reads`のバイト数の閾値。正の整数だけを採用し、それ以外は省略時の値を使う

ホストが定める次の環境変数は、`agent_toolkit/_common/host_homes.py`の関数だけが読む。

- `CLAUDE_CONFIG_DIR`: 空でない絶対パスならClaude Codeの設定ディレクトリとして使い、それ以外は`~/.claude`を使う。Function hooks module（`hooks/session_exit.ts`）も同じ規則で解決する
- `CODEX_HOME`: 空でなければCodexのホームとして使い、空か未設定なら`~/.codex`を使う

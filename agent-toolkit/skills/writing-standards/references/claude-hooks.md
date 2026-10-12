# Claude Code・Codex Hook実装ガイドライン

Hook実装はホストごとの公式契約へ適合させ、Claude Code固有の上限値や出力契約にはホスト名を付ける。
主題ごとに分けたhook実装の規定（遮断・警告の判定、出力フィールド、Stop/SubagentStop、メッセージの言語と標識）と読む時点は、`agent-toolkit:writing-standards`の`SKILL.md`の読込表が示す。

## hookスクリプトの基本プロトコル

一次資料はClaude Codeが<https://code.claude.com/docs/ja/hooks.md>（取得方法は`host-official-references.md`の「公式リファレンス（Claude Code）」）、Codexが<https://learn.chatgpt.com/docs/hooks>である。入力ペイロード（`transcript_path`・`last_assistant_message`・`agent_transcript_path`・`hookSpecificOutput`など）と出力形式はこれらから確定し、参照した節名を計画ファイルの実装者向け領域へ引用する（努力目標）。

- 入出力: stdinに呼び出しペイロードのJSONが渡され、stdoutにホスト別契約の応答JSONを出力する。exit codeは0で正常完了とする
- `${CLAUDE_PLUGIN_ROOT}`: Claude Codeが現プラグインのルートへ置換する。Codexもplugin hookの`command`内では置換するが、通常のスキル実行では置換しないため、スキル本文から実行するコマンドには読み込んだSKILL.mdの絶対パスから確定したplugin rootを用いる
- Codexの信頼確認: plugin同梱フックも定義の変更後は`/hooks`で内容を確認して信頼する。信頼するまでCodexはそのフックをスキップする
- 呼出主体の判別: サブエージェントとメイン会話の区別には共通入力の`agent_id`を使う。`transcript_path`はサブエージェント内で発火したフックでもメインセッションの記録を指し、サブエージェント自身の記録を指すのは`SubagentStop`の`agent_transcript_path`だけである。監査記録は`docs/development/audit-records.md`の「agent-toolkit/skills/writing-standards/references/claude-hooks.md：hookスクリプトの基本プロトコル：2026年9月2日」にある
  `SessionStart`は`agents_server`の委譲先でも発火し、`SubagentStart`は`Agent`ツールのサブエージェントの起動時だけ発火する。`agent-toolkit/agent_toolkit/_hooks/rules_context.py`は前者で環境変数`AGENT_TOOLKIT_DELEGATED_SESSION`と`AGENT_TOOLKIT_OWNER_SESSION`により委譲先を判別し、メイン向け条文を除く。後者ではサブエージェント向け条文を追加する。`Stop`と`SubagentStop`の判別は`claude-hooks-stop.md`「委譲先での発火」が定める
- そのターンの地の文の可視性: `PreToolUse`の発火時点では、同じアシスタントターンのテキストブロックが`transcript_path`のJSONLへ未書き込みである。そのターンの地の文を入力とする判定は`PreToolUse`以外のイベントへ置く。また思考ブロックとツール呼び出しだけのターンも記録されるため、直近の地の文を対象とする判定はテキストブロックを持たないターンを走査の対象から除いて遡る。監査記録は`docs/development/audit-records.md`の「agent-toolkit/skills/writing-standards/references/claude-hooks.md：hookスクリプトの基本プロトコル：2026年9月6日」にある
- 発火条件: フック追加を計画に含める場合は、対象イベントの発火条件を計画の実装者向け領域へ明示する。PostToolUseはツール成功時のみ発火する。PostToolUseFailureは実行を始めたツールの失敗時に発火して`additionalContext`を受理し、入力検証による拒否と権限拒否では発火しない。PermissionDeniedはauto modeの拒否だけで発火し（deny規則への一致、手動の拒否、`PreToolUse`の遮断は対象外）、出力は`hookSpecificOutput.retry`だけを受理するため、回復手順の本文は別の手段で届ける。監査記録は`docs/development/audit-records.md`の「agent-toolkit/skills/writing-standards/references/claude-hooks.md：hookスクリプトの基本プロトコル：2026年9月8日」にある。PostToolUseFailureとPermissionDeniedの発火条件の記録は同ファイルの「agent-toolkit/skills/writing-standards/references/claude-hooks.md：hookスクリプトの基本プロトコル：2026年10月7日」にある
- CodexのPostToolUseは`tool_response`を任意のJSON値として渡す。シェル実行では終了コードを含まず出力文字列だけが届くため、状態記録の条件からコマンドの成否を外す。`apply_patch`は適用成功時だけ発火するため、編集成功後の状態記録へ利用できる
- Bashコマンドを対象とするhookの判定方式と、出力有無を状態で切り替えるフックの計画への記載は`claude-hooks-block-warn.md`「遮断・警告フックの成立条件」が定める
- 起動パスの互換: hook定義が`command`で参照するスクリプトを改名、移動または削除する場合は、旧パスへ新しいエントリーポイントを呼ぶだけの互換スクリプトを残す。稼働中のセッションは起動時に読んだ旧パスを参照し続け、実体が無いとツール呼び出しがフック実行の失敗で拒否される。共通エントリーポイントが未知のサブコマンドを終了コード0で通過させる構成では、サブコマンドの削除に互換は要らない。互換スクリプトのdocstringへ役割と撤去条件を書き、撤去は旧定義を読み込んだセッションの全終了を確認できた場合だけ行う（配布物では新エントリーポイントを含む版の配布後の版数更新以降が下限）
- イベントごとのエントリーポイント: 同じイベントへの判定は新しい登録を並べず、既存のエントリーポイントへ相乗りさせる（努力目標。ホストは登録の件数だけプロセスを起動し、実行時間と画面の行数が増える）。エントリーポイントは判定単位で例外を隔離して単一の応答へ集約し、matcherによる限定は各判定側の早期returnへ移す

## matcher設定

ツール名で`matcher`を評価するイベントは`PreToolUse`、`PostToolUse`、`PostToolUseFailure`、`PermissionRequest`、`PermissionDenied`の5つとする。
`"*"`、空文字列およびキーの省略は全ツールへ一致する。英数字、`_`、`-`、空白、`,`、`|`だけからなる値は`|`または`,`で区切ったツール名の完全一致、それ以外の文字を含む値は先頭と末尾を固定しないJavaScriptの正規表現として評価する。
全ツールへ一致させる新規の登録には`"*"`を書き（努力目標）、ツール名で評価しないイベントでは`matcher`キーを省く。
一次資料は<https://code.claude.com/docs/en/hooks.md>の`Matcher patterns`節である。監査記録は`docs/development/audit-records.md`の「agent-toolkit/skills/writing-standards/references/claude-hooks.md：matcher設定：2026年9月4日」にある。

- `matcher`を広げた場合は、スクリプト側で`tool_name`を確認して対象外を早期returnする
- Codexへはツール名を明示して限定したmatcherを配布する。Claude Code向けの全ツール一致のmatcherをそのまま渡すと、入力契約を確認していないツールでもhandlerが起動する

## Codexの編集ツール入力

Codexの`apply_patch`はmatcher上で`Edit`・`Write`の別名に一致するが、入力payloadの`tool_name`は`apply_patch`のままであり、変更本文は`tool_input.command`へ`*** Begin Patch`から`*** End Patch`までの構文で入る。
1回の呼び出しに`Add File`・`Update File`・`Delete File`・`Move to`と`@@`区切りのhunkを複数ファイル分含む。

ホスト差をチェック本体へ持ち込まないため、編集入力は次の2層で正規化する。

- 操作記録: 入力だけから操作種別、対象パス、順序付き編集断片を求める。ファイルを読まないためPostToolUseからも使える
- 変更前後像: 対象ファイルの現在内容へ操作記録を適用して全文を組み立てる。PreToolUseだけが使う

相対パスはpayloadの`cwd`起点で解決する。patchを解釈できない場合はhook側で遮断せず、妥当性判定を`apply_patch`本体へ委ねる。

ホスト判定は、Codexがターン単位hookへ付加する非空文字列の`turn_id`と、UserPromptSubmitでCodexだけが渡す`prompt`と`model`の組に限り、ツール名から推測しない。

複数ファイル・複数チェックの警告は1つの`hookSpecificOutput.additionalContext`へ結合して返す。stdout全体が1つのJSONとして解析されるためである。

Codexのシェル実行と統合実行（`exec_command`）はmatcher上で`Bash`に一致し、`tool_input.command`にコマンド文字列が入る。一次資料は<https://learn.chatgpt.com/docs/hooks>のTool coverageとし、matcherへ列挙する名前を`Bash`に限る。

## 環境変数の一覧

配布物完結の環境変数（`AGENT_TOOLKIT_<PURPOSE>`形式）の一覧を示す。
変数名を読み書きする処理は、private-notesを`agent_toolkit/_common/private_notes.py`、委譲先の印と委譲元の識別子を`agent_toolkit/_common/delegated_session.py`が持ち、他の処理はそれらの定数と関数を使う。

- `AGENT_TOOLKIT_PRIVATE_NOTES`: `atk wi`管理repoのroot（指定がなければ`~/private-notes/`）
- `AGENT_TOOLKIT_STOP_GATE_DEBUG`: デバッグ出力
- `AGENT_TOOLKIT_HOOK_PAYLOAD_DUMP`: 受信payloadのダンプ先
- `AGENT_TOOLKIT_RESTART_SPEC`: AWI処理の常駐実行で、次に起動するセッションの指定を起動側の処理へ渡す一時ファイルのパス
- `AGENT_TOOLKIT_DELEGATED_SESSION`: Claude Code（backend）の委譲先として起動したセッションの印（値`1`）。委譲先かの判定（`is_delegated`）は、この印か次項の識別子のどちらかを持つ場合を委譲先とし、常駐実行の終了保証を最上位セッションへ限定する判定などに使う
- `AGENT_TOOLKIT_OWNER_SESSION`: 委譲先が取得または作成した計画バンドルの所有として記録する委譲元セッションの識別子。`agents_server`が起動した子だけが持つため、Codex backendの委譲先を含めてメイン向け規範の追加を省く判定にも使う
- `AGENT_TOOLKIT_PROCESS_LOOP_SESSION`: AWI処理の常駐実行が起動したセッションの印（値`1`）。次項のIDがある場合は、hook入力の会話IDとの一致も確認する
- `AGENT_TOOLKIT_PROCESS_LOOP_SESSION_ID`: process-loopがClaude会話の新規起動またはID指定再開で子へ渡す会話ID。hook入力の`session_id`と比べ、環境印を継承した入れ子の別会話を自律終了、空転ガード、計画保存通知、セッション名および観測ログの対象から外す
- `AGENT_TOOLKIT_PROCESS_LOOP_INSTRUCTION`: 常駐実行がセッション起動時に渡す追加指示の本文。`rules_context`が委譲先を除くメインのセッション開始時の文脈へ置く
- `AGENT_TOOLKIT_LARGE_READ_BYTES`: CodexのBashによる全文取得を分割読取へ誘導する`pretooluse/large_reads`のバイト数の閾値。正の整数だけを採用し、それ以外は省略時の値を使う

ホストが定める次の環境変数は、`agent_toolkit/_common/host_homes.py`の関数だけが読む。

- `CLAUDE_CONFIG_DIR`: 空でない絶対パスならClaude Codeの設定ディレクトリとして使い、それ以外は`~/.claude`を使う。Function hooks module（`hooks/session_exit.ts`）も同じ規則で解決する
- `CODEX_HOME`: 空でなければCodexのホームとして使い、空か未設定なら`~/.codex`を使う

# Claude Code・Codex Hook実装ガイドライン

Hook実装はホストごとの公式契約へ適合させる。
Claude Code固有の上限値や出力契約にはホスト名を付ける。
本書から主題ごとに分けたhook実装の規定（遮断・警告の判定、出力フィールド、Stop/SubagentStop、メッセージの言語と標識）の文書と読む時点は、`agent-toolkit:writing-standards`の`SKILL.md`の読込表が示す。

## hookスクリプトの基本プロトコル

matcher・出力フィールド・メッセージ標識の記述指示が前提とする最低限の実装規約を示す。
Claude Codeは公式ドキュメント<https://code.claude.com/docs/ja/hooks.md>を一次資料とする。
取得方法は`agent-skills.md`の「公式リファレンス（Claude Code）」が定める。
Codexは公式ドキュメント<https://learn.chatgpt.com/docs/hooks>を一次資料とする。
参照対象は入力ペイロード仕様（`transcript_path`・`last_assistant_message`・`agent_transcript_path`・`hookSpecificOutput`等）と出力形式仕様とする。
参照したセクション名は計画ファイルの実装者向け領域へ引用する（努力目標。計画を読む実装者とレビュー担当が根拠の節をたどれるようにするため）。
payload設計は、上記の一次資料が示す仕様から確定する。

- 入出力: stdinに呼び出しペイロードのJSONが渡され、stdoutにホスト別契約の応答JSONを出力する。exit codeは0で正常完了とする
- `${CLAUDE_PLUGIN_ROOT}`: Claude Codeランタイムが現プラグインのルートディレクトリに置換する組み込み変数である。
  Codexもplugin hookの`command`内では互換変数として置換する。通常のCodexスキル実行では置換されないため、
  スキル本文から実行するコマンドには、読み込んだSKILL.mdの絶対パスから確定したplugin rootを用いる
- Codexの信頼確認: plugin同梱フックも定義の変更後は`/hooks`で内容を確認して信頼する。
  信頼するまではCodexがそのフックをスキップする
- 呼出主体の判別: サブエージェントの呼び出しとメイン会話を区別する場合は共通入力の`agent_id`を使う。`transcript_path`はサブエージェント内で発火したフックでもメインセッションの記録を指すため判別に利用できない。サブエージェント自身の記録を指すのは`SubagentStop`の`agent_transcript_path`だけである。監査記録は`docs/development/audit-records.md`の「agent-toolkit/skills/writing-standards/references/claude-hooks.md：hookスクリプトの基本プロトコル：2026年9月2日」にある
- そのターンの地の文の可視性: `PreToolUse`の発火時点では、そのツール呼び出しと同じアシスタントターンのテキストブロックが`transcript_path`のJSONLへ未書き込みである。
  思考ブロックとツール呼び出しだけのターンも記録されるため、直前の1ターンだけを判定対象にすると地の文を取得できない。
  そのターンの地の文を入力とする判定を`PreToolUse`へ置かない。
  直近の地の文を対象とする判定では、テキストブロックを持たないターンを走査の対象から除いて遡る。
  監査記録は`docs/development/audit-records.md`の「agent-toolkit/skills/writing-standards/references/claude-hooks.md：hookスクリプトの基本プロトコル：2026年9月6日」にある
- フック追加を計画に含める場合、対象イベントの発火条件を計画の実装者向け領域へ事前明示する。
  例えばPostToolUseはツール成功時のみ発火する。
  PostToolUseFailureは実行を始めたツールが失敗したときに発火し、`additionalContext`を受理する。入力検証による拒否と権限拒否は、実行前に退けられるためPostToolUseFailureの発火の対象外である。
  PermissionDeniedの発火はauto modeの拒否に限られ、deny規則への一致、手動の拒否と`PreToolUse`の遮断は対象外である。出力は`hookSpecificOutput.retry`だけを受理するため、失敗時の回復手順の本文は別の手段で届ける。
  監査記録は`docs/development/audit-records.md`の「agent-toolkit/skills/writing-standards/references/claude-hooks.md：hookスクリプトの基本プロトコル：2026年9月8日」にある。
  PostToolUseFailureとPermissionDeniedの発火条件の記録は同ファイルの「agent-toolkit/skills/writing-standards/references/claude-hooks.md：hookスクリプトの基本プロトコル：2026年10月7日」にある
- CodexのPostToolUseは`tool_response`を任意のJSON値として渡す。シェル実行では終了コードを含まず
  出力文字列だけが届くため、状態記録の条件からコマンドの成否を外す。
  `apply_patch`は適用に成功した場合だけ発火するため、編集成功後の状態記録へ利用できる
- Bashコマンドを対象とするhookの判定は、コマンド文字列全体への部分一致で発火させず、
  区間分割とトークン化により対象が実行位置にある場合だけ発火させる
  （検索語・引数として名前が現れるだけの読み取り操作を検出しないため）。
  実行を遮断するチェックは、コマンド置換・サブシェル・オプション終端まで解決できる解析を用意できる場合に限り
  同じ判定へ移す。用意できない間は過検出を許容する現行判定を維持し、過検出の費用は`claude-hooks-block-warn.md`「遮断・警告フックの成立条件」の比較で評価する（解析の不足だけを理由に保護を外さないため）
- 観測した状態に応じて警告またはblockの出力有無を切り替えるフックを計画に含める場合、
  別リポジトリ、別worktree、複数主体の同時実行などの条件が誤って成立する入力と誤って成立しない入力を列挙する。
  各入力の期待動作と検証方法を計画の実装者向け領域へ記載する
- hook定義が`command`で参照するスクリプトのパスを改名、移動または削除する場合は、旧パスへ新しいエントリーポイントを呼び出すだけの互換スクリプトを残す。
  hook定義はセッション起動時に読み込まれ、稼働中のセッションは旧パスを参照し続けるため、実体を失うとそのセッションのツール呼び出しがフック実行の失敗で拒否される。
  hook定義が共通エントリーポイントへサブコマンドを渡し、共通エントリーポイントが未知のサブコマンドを終了コード0で通過させる場合は、
  サブコマンドの削除でツール呼び出しが拒否されないため、旧サブコマンド名の実体を残さなくてよい。
  互換スクリプトのdocstringへ役割と撤去条件を書く。撤去は旧定義を読み込んだセッションが全て終了したことを確認できた場合だけ行い、
  確認できない場合は互換スクリプトを維持する。
  配布物では、新しいエントリーポイントを含む版を配布した後の版数更新以降を撤去可能な時機の下限とし、撤去の契機は旧定義を読み込んだセッションの全終了の確認とする
- イベントごとのエントリーポイント: 同じイベントへ判定を追加する場合は、新しい登録を並べる代わりにそのイベントの既存のエントリーポイントへ相乗りさせる（努力目標。登録ごとにプロセスが起動するため）。
  エントリーポイントは各判定を順に呼び、判定単位で例外を隔離し、単一の応答へ集約する。
  ホストは登録の件数だけプロセスを起動するため、登録を分けると実行時間と画面の行数が判定の件数に比例して増える。
  matcherを持つ登録を統合する場合は、ツール名による限定を各判定側の早期returnへ移す

## matcher設定

ツール名で`matcher`を評価するイベントは`PreToolUse`、`PostToolUse`、`PostToolUseFailure`、`PermissionRequest`、`PermissionDenied`の5つとする。
これらのイベントの`matcher`は3通りに解釈する。`"*"`、空文字列およびキーの省略は全ツールへ一致する。英数字、`_`、`-`、空白、`,`、`|`だけからなる値は、`|`または`,`で区切ったツール名の完全一致とする。それ以外の文字を含む値は、先頭と末尾を固定しないJavaScriptの正規表現として評価する。
全ツールへ一致させる新規の登録には`"*"`を書く（努力目標。3通りの表記は同じ意味で、そろえると読み手が登録を比べやすいため）。ツール名で`matcher`を評価しないイベントでは`matcher`キーを省く。
一次資料は公式ドキュメント<https://code.claude.com/docs/en/hooks.md>の`Matcher patterns`節とする。
監査記録は`docs/development/audit-records.md`の「agent-toolkit/skills/writing-standards/references/claude-hooks.md：matcher設定：2026年9月4日」にある。

- 個別の早期returnガード: `matcher`を広げた場合、hookスクリプト側で`tool_name`を
  確認し対象外を早期returnすることで処理コストと誤検出を抑える
- ホスト間でmatcherを共有しない: Claude Code向けの全ツール一致のmatcherをCodexへそのまま配布すると、
  入力契約を確認していないツールでもhandlerが起動する。Codexへ射影する場合はツール名を明示して限定する

## Codexの編集ツール入力

Codexの`apply_patch`はmatcher上で`Edit`・`Write`の別名に一致する。
一方で入力payloadの`tool_name`は`apply_patch`のままであり、変更本文は`tool_input.command`へ
`*** Begin Patch`から`*** End Patch`までの構文で入る。
`Add File`・`Update File`・`Delete File`・`Move to`と`@@`区切りのhunkを1回の呼び出しで複数ファイル分含む。

ホスト差をチェック本体へ持ち込まないため、編集入力は次の2層で正規化する。

- 操作記録: 入力だけから操作種別、対象パス、順序付き編集断片を求める。ファイルを読まないため
  PostToolUse（適用後）からも安全に利用できる
- 変更前後像: 対象ファイルの現在内容へ操作記録を適用して全文を組み立てる。PreToolUse（適用前）だけが使う

patch構文は`apply_patch`の構文として解釈する。相対パスはpayloadの`cwd`起点で解決する。
patchを解釈できない場合はhook側で操作を遮断せず、妥当性判定を`apply_patch`本体へ委ねる。

ホスト判定はCodexがターン単位hookへ付加する非空文字列の`turn_id`を基準とする。
ホスト判定に用いる入力はこの`turn_id`に限る。

複数ファイル・複数チェックの警告は1つの`hookSpecificOutput.additionalContext`へ結合して返す。
stdout全体が1つのJSONとして解析されるため、対象ごとに出力すると複数JSONとなり解析に失敗する。

Codexのシェル実行は、matcher上で`Bash`に一致する。
統合実行（`exec_command`）も同じく`Bash`に一致する。
入力payloadの`tool_input.command`にはコマンド文字列が入る。
一次資料は<https://learn.chatgpt.com/docs/hooks>のTool coverageとし、matcherへ列挙する名前を`Bash`に限る。

## 環境変数の一覧

配布物完結の環境変数（`AGENT_TOOLKIT_<PURPOSE>`形式）の一覧と用途を示す。

- `AGENT_TOOLKIT_PRIVATE_NOTES`: `atk wi`管理repoのroot（指定がなければ`~/private-notes/`）
- `AGENT_TOOLKIT_STOP_GATE_DEBUG`: デバッグ出力
- `AGENT_TOOLKIT_HOOK_PAYLOAD_DUMP`: 受信payloadのダンプ先
- `AGENT_TOOLKIT_RESTART_SPEC`: AWI処理の常駐実行で、次に起動するセッションの指定を
  起動側の処理へ渡す一時ファイルのパス
- `AGENT_TOOLKIT_DELEGATED_SESSION`: 委譲先として起動したセッションであることを示す印。常駐実行の終了保証を最上位セッションへ限定する判定に使う
- `AGENT_TOOLKIT_OWNER_SESSION`: 委譲先が取得または作成した計画バンドルの所有として記録する、委譲元セッションの識別子。`agents_server`が起動した子だけが持つため、Codex backendの委譲先を含めてメイン向け規範の追加を省く判定にも使う
- `AGENT_TOOLKIT_PROCESS_LOOP_SESSION`: AWI処理の常駐実行が起動したセッションの印（値`1`）。常駐用hookは次項のIDがある場合、印に加えてhook入力の会話IDとの一致を確認する。IDを指定しない再開では印だけで判定する
- `AGENT_TOOLKIT_PROCESS_LOOP_SESSION_ID`: process-loopがClaude会話の新規起動またはID指定再開で子へ渡す会話ID。hook入力の`session_id`と比べ、環境印を継承した入れ子の別会話を自律終了、空転ガード、計画保存通知、セッション名および観測ログの対象から外す
- `AGENT_TOOLKIT_PROCESS_LOOP_INSTRUCTION`: 常駐実行がセッション起動時に渡す追加指示の本文。`rules_context`が委譲先を除くメインのセッション開始時の文脈へ置く
- `AGENT_TOOLKIT_LARGE_READ_BYTES`: CodexのBashによる全文取得を分割読取へ誘導する`pretooluse/large_reads`のバイト数の閾値。正の整数だけを採用し、それ以外は省略時の値を使う

## セッション状態ファイル

hook間で情報を共有するセッション状態ファイルの設計、寿命、排他制御および個別フラグの記録元と利用先は`session-state-and-flags.md`が定める。

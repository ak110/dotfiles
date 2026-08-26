# Claude Code・Codex Hook実装ガイドライン

Hook実装はホストごとの公式契約へ適合させる。
Claude Code固有の上限値や出力契約にはホスト名を付ける。

## hookスクリプトの基本プロトコル

matcher・出力フィールド・メッセージ標識の記述指示が前提とする最低限の実装規約を示す。
Claude Codeは公式ドキュメント<https://code.claude.com/docs/ja/hooks.md>を一次資料とする。
取得方法は`agent-toolkit/skills/agent-standards/references/agent-skills.md`「公式リファレンス（Claude Code）」節に従う。
Codexは公式ドキュメント<https://learn.chatgpt.com/docs/hooks>を一次資料とする。
参照対象は入力ペイロード仕様（`transcript_path`・`last_assistant_message`・`agent_transcript_path`・`hookSpecificOutput`等）と出力形式仕様とする。
参照したセクション名は計画ファイルの実装者向け領域へ引用する。
既存実装の類推・記憶ベースでのpayload設計は用いない。

- 入出力: stdinに呼び出しペイロードのJSONが渡され、stdoutにホスト別契約の応答JSONを出力する。exit codeは0で正常完了とする
  - stderr経由の表示はexit 2との組合せで使う代替経路
- `${CLAUDE_PLUGIN_ROOT}`: Claude Codeランタイムが現プラグインのルートディレクトリに置換する組み込み変数である。
  Codexもplugin hookの`command`内では互換変数として置換する。通常のCodexスキル実行では置換されないため、
  スキル本文から実行するコマンドには、読み込んだSKILL.mdの絶対パスから確定したplugin rootを用いる
- Codexの信頼確認: plugin同梱フックも定義の変更後は`/hooks`で内容を確認して信頼する。
  信頼するまではCodexが当該フックをスキップする
- 出力フィールドの併用: deny時の`permissionDecisionReason`と`hookSpecificOutput.additionalContext`はどちらもコーディングエージェントに届く。一方で十分なため、重複表示を避け片方に統一する
- フック追加を計画に含める場合、対象イベントの発火条件を計画の実装者向け領域へ事前明示する。
  例えばPostToolUseはツール成功時のみ発火し、失敗時はPostToolUseFailureが処理する。
  auto modeでのブロック等はPermissionDeniedフックが処理する
- CodexのPostToolUseは`tool_response`を任意のJSON値として渡す。シェル実行では終了コードを含まず
  出力文字列だけが届くため、コマンドの成否を前提とする状態記録へ使わない。
  `apply_patch`は適用に成功した場合だけ発火するため、編集成功後の状態記録へ利用できる
- Bashコマンドを対象とする検査は、コマンド文字列全体への部分一致で発火させず、
  区間分割とトークン化により対象が実行位置にある場合だけ発火させる
  （検索語・引数として名前が現れるだけの読み取り操作を検出しないため）。
  実行を遮断する検査は、コマンド置換・サブシェル・オプション終端まで解決できる解析を用意できる場合に限り
  同じ判定へ移す。用意できない間は過検出を許容する現行判定を維持する（解析の不足で保護を外さないため）
- 観測した状態に応じて警告又はblockの出力有無を切り替えるフックを計画に含める場合、
  別リポジトリ、別worktree、複数主体の同時実行などの条件が誤って成立する入力と誤って成立しない入力を列挙する。
  各入力の期待動作と検査を計画の実装者向け領域へ記載する

## matcher設定

`hooks.json`の`PreToolUse` / `PostToolUse`の`matcher`（ツール名への正規表現評価、
空文字列で全ツール対象）は公式ドキュメント<https://code.claude.com/docs/ja/hooks.md>の
matcher仕様を一次資料として参照する。

- 個別の早期returnガード: `matcher`を広げた場合、hookスクリプト側で`tool_name`を
  確認し対象外を早期returnすることで処理コストと誤検出を抑える
- ホスト間でmatcherを共有しない: Claude Code向けの空matcher（全ツール対象）をCodexへそのまま配布すると、
  入力契約を確認していないツールでもhandlerが起動する。Codexへ射影する場合はツール名を明示して限定する

## Codexの編集ツール入力

Codexの`apply_patch`は、matcher上で`Edit`・`Write`の別名に一致する。
一方で入力payloadの`tool_name`は`apply_patch`のままであり、変更本文は`tool_input.command`へ
`*** Begin Patch`から`*** End Patch`までの構文で入る。
`Add File`・`Update File`・`Delete File`・`Move to`と`@@`区切りのhunkを1回の呼び出しで複数ファイル分含む。

ホスト差を検査本体へ持ち込まないため、編集入力は次の2層で正規化する。

- 操作記録: 入力だけから操作種別、対象パス、順序付き編集断片を求める。ファイルを読まないため
  PostToolUse（適用後）からも安全に利用できる
- 変更前後像: 対象ファイルの現在内容へ操作記録を適用して全文を組み立てる。PreToolUse（適用前）だけが使う

patch構文をシェル構文として評価しない。相対パスはpayloadの`cwd`起点で解決する。
patchを解釈できない場合はhook側で操作を遮断せず、妥当性判定を`apply_patch`本体へ委ねる。

ホスト判定は、Codexがターン単位hookへ付加する非空文字列の`turn_id`を正本とする。
`model`の有無やツール名の推測を別の判定として併設しない。

複数ファイル・複数検査の警告は1つの`hookSpecificOutput.additionalContext`へ結合して返す。
stdout全体が1つのJSONとして解析されるため、対象ごとに出力すると複数JSONとなり解析に失敗する。

## 出力フィールドの使い分け

各フィールドのスキーマとイベント別の対応可否は公式ドキュメント
<https://code.claude.com/docs/ja/hooks.md>を一次資料とする。
本節は経路選択の方針だけを定める。

PreToolUseやPostToolUseでコーディングエージェントに行動を促す場合は`hookSpecificOutput.additionalContext`を第一経路として使う（`_llm_notice`ヘルパー経由の本文構築を推奨）。
`systemMessage`は使わず、stderr出力は`exit 2`のblockと組み合わせる場合のみに限定する。
`systemMessage`の情報通知は利用者の判断・操作に影響する事象に限って使い、決定論的で失敗しない自動補正の発動など、反復発動して利用者の対応を要しない事象には付けない。
Stop/SubagentStopで当該ターン継続を強制する用途（振り返り誘導等）は`decision: "block"`＋`reason`を採用する。
永続ログはstderr出力ではなく`_stop_gate.append_stop_log`等の専用APIに集約する。

| フィールド | 表示先 | 用途 |
| --- | --- | --- |
| `hookSpecificOutput.additionalContext` | コーディングエージェント | フィードバックを渡す主経路。フックエラー扱いとならずターン継続を妨げない |
| `reason` | コーディングエージェント（`decision: "block"`時のみ） | blockを併用する場合の理由欄 |
| `permissionDecisionReason` | deny時はコーディングエージェント、allow/ask時はユーザーのみ | PreToolUseの決定理由 |
| `systemMessage`・`stopReason` | ユーザーのみ | 情報通知と`continue: false`時の終了メッセージ |
| `decision.*` | PermissionRequest専用 | 許可・拒否の決定。`hookSpecificOutput`直下に置く |

`decision: "block"`の挙動はイベント別に異なる。
Stop/SubagentStopでは停止を防いでターン継続を強制し、PostToolUseではblock理由を直前のツール結果に添えて返す。
挙動の強制が不要であれば`additionalContext`単独で出力する。

- block通知は`_hook_notice`のblock専用整形関数（`block_formatter`）で生成し、解消手段の`fix`を渡す。`fix`が空文字列または空白文字だけの場合は`ValueError`となる
- 独自の整形関数でblock本文を構成しない（解消手段の欠落を機械的に検出できなくなるため）
- PreToolUse・PostToolUseのblockは当該操作の中止で場面が解消するため、Stop系の成立条件の規定は適用しない

警告専用のPreToolUse出力は`hookSpecificOutput.additionalContext`だけを返し、`permissionDecision`を省略する。
決定を省略すると通常の権限フローが適用され、警告表示が許可プロンプトを省略しない。

組み込みのdeny / askルールはhookの戻り値に関わらず評価される。
`.claude/`配下への書き込み確認等の組み込みaskルールはPreToolUseの`allow`では上書きできない。
確認ダイアログを抑制したい場合はPermissionRequestイベントで`decision.behavior: "allow"`を返す。

`updatedInput`による入力書き換えは、確認ダイアログの発生自体を抑止しない。
ダイアログを伴う値を拒否する必要がある場合は書き換えでなくブロックで扱う。
`agents_server`では`engine`に応じたバックエンドをMCPサーバーが選択する。承認、ユーザー入力、認証更新及び一覧操作は公開せず、実行中turnの明示的な中断だけをsession単位の`kill`として公開する。
PreToolUseは`start`の絶対`cwd`と`wait`・`send_message`・`kill`の保存済みsessionを検査するだけで、入力の実行権限値を自動補正しない。
PostToolUseは成功した`start`のcwdと、`wait`・`send_message`・`kill`のsession状態を記録する。失敗時は状態を変更せず、既存の開始点用
`PostToolUseFailure` matcherを拡張しない。
旧blocking MCPの入力例 `` `sandbox: danger-full-access` `` は移行説明と保護対象の識別にだけ残し、新経路へ渡さない。

エージェントへ特定の行動・引数を要求するblockを新設する場合は、要求する要件を実行主体が事前に読み得る規範文書（常時ロードのルール、または当該作業で起動されるスキルの本文・参照文書）へ明示する。遮断メッセージだけを要件の初出にしない。
blockの新設時は、対象環境で文書化済みの正式コマンド形（スキル・`AGENTS.md`・タスクランナー定義が指定する起動形）への発動有無を確認し、正当な運用が遮断される場合は判定条件を見直してから導入する。

block検査は、規範の読み込み漏れや手順の取り違えを実行主体へ通知する目的で設計する。
別ツール経由の書き換えやフック自身への変更など、迂回経路の網羅的な遮断を目的とする検査は新設しない。
block文面には検出した原因と、遮断を解除して続行する承認済みの経路を示す。

### PermissionRequest

確認ダイアログ表示時に発火するイベント。ユーザーに代わって許可 / 拒否を決定するときに使う。
スキーマがPreToolUseと異なり、`hookSpecificOutput`直下に`decision`オブジェクトを置く。
`hookEventName`は`"PermissionRequest"`を指定する。

組み込みdenyルールは`allow`でも上書きできないが、確認ダイアログ（ask相当）はスキップできる。
`matcher`はツール名で評価する（`Bash` / `Edit|Write`等）。
入力payloadは`tool_name` / `tool_input`に加え、`permission_suggestions`配列を受け取る。

### UserPromptSubmit

`hookSpecificOutput.additionalContext`を当該ターンの応答生成前にユーザー発話へ前置注入する誘導に使う。
本イベントは`decision: "block"`へ対応しない。
Claude Codeでは`decision`と独立に注入可能で、stdoutプレーン出力もコンテキストへ追加される。
Codexでは`hookSpecificOutput.hookEventName`を`UserPromptSubmit`とし、`additionalContext`を返す。

Claude CodeのUserPromptSubmit payloadから現在のセッション名を取得する入力値は得られない。
計画ファイルを扱うhookは、同一セッションでまだ出力していない場合だけ計画ファイル名のstemを
`sessionTitle`へ一度だけ出力する。
`sessionTitle`と`additionalContext`が同じ呼び出しで必要な場合は、`hookSpecificOutput`へ両方を含む1つのJSONを返す。
この契約はClaude Code専用であり、Codex payload（`model`又はCodexのターン識別子を持つ入力）では
`sessionTitle`を出力しない。

## Stop/SubagentStopフックの再帰呼び出し対策

Stop/SubagentStopフックは、入力payloadの`stop_hook_active`が真の場合、
判定処理を行わず終了を許可する応答を返す。出力経路によらず両イベントで必須とする。
`stop_hook_active`は、直前の同フック呼び出しが当該ターンの終了を一度阻止したことを示す。

Stop/SubagentStopの`decision: "block"`は、対象主体が同一ターン内の行動で解消できる条件に限る（厳守規定）。
外部事象の完了、他主体の稼働状態、直前ターンで確定済みの内容など、対象主体の行動で変えられない状態を条件にしない。
待機中の主体が終了を拒否されると、ターンを終える以外の動作が残らず、無操作のツール呼び出しの反復に陥る。

この対策を要する理由は次のとおりである。
フックがターン終了を阻止するとコーディングエージェントは新たな応答を生成し、
その応答に対して同じフックが再び発火する。
判定条件が変化しない場合、この繰り返しはClaude Codeの既定上限（連続8回）まで続く。
上限に達すると警告とともにフックの判定が無視されてターンが終了する。
上限値は`CLAUDE_CODE_STOP_HOOK_BLOCK_CAP`環境変数で変更できる。

出力経路は用途で選ぶ。
次のユーザー入力ターンまで待ってよい誘導は`hookSpecificOutput.additionalContext`を用いる。
当該ターンの継続を強制する誘導（振り返りスキルの起動など）は`decision: "block"`と`reason`を用いる。
いずれの経路もStop・SubagentStopの双方で採用できる。

ターン終了の言語的判定（完了文言・質問・待機表明の判別）をフック側のコードで
正規表現等により行うと誤検知が生じやすい。
コーディングエージェントへの誘導文の先頭に判定基準を事前チェックとして埋め込み、
基準を満たさない場合は誘導内容に従わずターンを終了する設計を推奨する。

CodexのStopは`decision: "block"`と`reason`で同一ターンを継続し、許可時は空のJSONオブジェクトを返す。
Codex固有の入力には`model`があり、Stopでは`stop_hook_active`と`last_assistant_message`も受け取る。
Codex rolloutのtranscript形式は安定インターフェースではないため、完了判定や背景作業判定の契約に使わない。
状態欠落時の回復判定など、必要な標識の有無を確認する限定用途でだけruntime別に変換する。

## 環境変数の一覧

配布物完結の環境変数（`AGENT_TOOLKIT_<PURPOSE>`形式）の一覧と用途を示す。

- `AGENT_TOOLKIT_PRIVATE_NOTES`: `atk mq`管理repoのroot（既定`~/private-notes/`）
- `AGENT_TOOLKIT_STOP_GATE_DEBUG`: デバッグ出力
- `AGENT_TOOLKIT_HOOK_PAYLOAD_DUMP`: 受信payloadのダンプ先
- `AGENT_TOOLKIT_RESTART_SPEC`: フィードバック処理の常駐実行で、次に起動するセッションの指定を
  起動側の処理へ渡す一時ファイルのパス

## メッセージの記述言語

コーディングエージェントに直接渡る出力（`reason` / `additionalContext` / exit 2のstderr）は
英語で記述する。全文章を日本語で書く原則に対する意図的例外である。
ユーザーが目を通す必要のないシステム出力であることを、英語表記によって明示する。

ただし、hookメッセージ中で原本ファイル（`01-agent.md`・`CLAUDE.md`等）の章名・節名・キーワードを参照する場合は、
原本表記をそのまま引用する。
英訳した参照名（例:「日本語」節を`Japanese section`と訳すなど）は
原本の章名変更時に追従漏れの起点となるため使わない。
hookメッセージの目的はコーディングエージェントが参照先を特定できることであり、hookメッセージ全体の厳密な英語化ではない。

## コーディングエージェント宛てメッセージの標識

コーディングエージェントに直接渡る出力（`reason` / `additionalContext` / exit 2のstderr）には、
自動生成であることを明示するプレフィックスとサフィックスを付ける。
hookの出力はユーザー発言と同じ形で会話コンテキストに注入されるため、指示として誤認されないよう二重の標識を設ける。
書式（プレフィックス・サフィックス・ヘルパー関数の実装例）は
[hook-message-labeling.md](hook-message-labeling.md)を参照する。

## セッション状態ファイル

Claude CodeまたはCodexのhook間で情報を共有する場合、セッション単位の状態ファイルを使う。
hookは1呼び出しごとに独立プロセスとして起動するため、メモリー上の変数では情報の引き継ぎができない。

- パス規則: `{tempdir}/{plugin名など}-{session_id}.json`
  `tempfile.gettempdir()`と`payload["session_id"]`から組み立てる
- 形式: 単一のJSONオブジェクト。フラグ名はsnake_caseで統一する
- 書き込み: PostToolUseで観測したイベント（テスト実行・スキル呼び出しなど）をフラグとして記録する
- 読み取り: PreToolUseで判定材料として参照する（例: テスト未実行警告・スキル先行呼び出し催促）
- 破損・不在時: 空辞書として扱い、安全側の判定にフォールバックする
- 設計原則: フックイベント間の多段同期（コマンド文字列の完全一致検出とハッシュ照合の組合せ等）を状態ファイルへ持ち込まない。
  検査は対象ファイル実体への直接実行・直接読み取りで代替し、フラグは実施済み・読了済みの単純な記録に限定する
- フラグの用途・書き込み元・読み取り元の対応表をプラグインごとにドキュメント化する。
  `agent-toolkit`自身の一覧SSOTは`session-state-flags.md`に置き、本ファイルへ再掲しない
- 通常状態の期限より長く保持する記録は通常状態JSONへ混在させず、用途別の保存先と排他ロックへ分離する。
  `agent-toolkit`の計画名再出力抑止記録は`{tempdir}/claude-agent-toolkit-session-title/{session_id}.json`を使う

### 完了報告からの状態読み取り

完了報告本文から機械的に状態を読み取る場合、本文中の引用と区別できる位置へ
記録行を置く（厳守規定。検出位置を限定しないと自由記述本文中の引用が状態フラグを
誤って真化させ、完遂ゲート判定が機能不全に陥る）。完了報告末尾の連続した記録行ブロック、
または最終行へ検出対象を限定する。
自由記述の本文全体を検出対象に含めない。記録行の判定パターンは既知のキー
（例: `invoked_subagents`・`codex_unavailable`）へ限定する許可リスト方式とし、
英字キー全般に一致する汎用パターンは使わない。汎用パターンは、末尾付近に
他の`キー:`形式の散文が続く場合に引用ごと記録行と誤認する

### 並行書き込みの排他制御

Claude Codeは並列ツール呼び出しでhookを同時発火するため、複数プロセスから同一の状態ファイルへ書き込みが競合する。

- 通常状態の更新は排他ロック付きの`update_state`、計画名の記録は専用の`claim_session_title`を経由し、直接書き込むAPIを公開しない
- ロックファイルはどの経路でも削除しない。内容を持たない空ファイルであり、一時ディレクトリの通常の回収に委ねる
- 並行書き込みの回帰テストを維持する

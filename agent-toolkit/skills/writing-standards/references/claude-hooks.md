# Claude Code・Codex Hook実装ガイドライン

Hook実装はホストごとの公式契約へ適合させる。
Claude Code固有の上限値や出力契約にはホスト名を付ける。

## hookスクリプトの基本プロトコル

matcher・出力フィールド・メッセージ標識の記述指示が前提とする最低限の実装規約を示す。
Claude Codeは公式ドキュメント<https://code.claude.com/docs/ja/hooks.md>を一次資料とする。
取得方法は`agent-skills.md`の「公式リファレンス（Claude Code）」が定める。
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
- 呼出主体の判別: サブエージェントの呼び出しとメイン会話を区別する場合は共通入力の`agent_id`を使う。`transcript_path`はサブエージェント内で発火したフックでもメインセッションの記録を指すため判別に利用できない。サブエージェント自身の記録を指すのは`SubagentStop`の`agent_transcript_path`だけである。監査記録は`docs/development/audit-records.md`の「agent-toolkit/skills/writing-standards/references/claude-hooks.md：hookスクリプトの基本プロトコル：2026年9月2日」にある
- 当該ターンの地の文の可視性: `PreToolUse`の発火時点では、当該ツール呼び出しと同じアシスタントターンのテキストブロックが`transcript_path`のJSONLへ未書き込みである。
  思考ブロックとツール呼び出しだけのターンも記録されるため、直前の1ターンだけを判定対象にすると地の文を取得できない。
  当該ターンの地の文を入力とする判定を`PreToolUse`へ置かない。
  直近の地の文を対象とする判定では、テキストブロックを持たないターンを走査の対象から除いて遡る。
  監査記録は`docs/development/audit-records.md`の「agent-toolkit/skills/writing-standards/references/claude-hooks.md：hookスクリプトの基本プロトコル：2026年9月6日」にある
- 出力フィールドの併用: deny時の`permissionDecisionReason`と`hookSpecificOutput.additionalContext`はどちらもコーディングエージェントに届く。一方で十分なため、重複表示を避け片方に統一する
- フック追加を計画に含める場合、対象イベントの発火条件を計画の実装者向け領域へ事前明示する。
  例えばPostToolUseはツール成功時のみ発火し、失敗時はPostToolUseFailureが処理する。
  auto modeでのブロック等はPermissionDeniedフックが処理する。
  監査記録は`docs/development/audit-records.md`の「agent-toolkit/skills/writing-standards/references/claude-hooks.md：hookスクリプトの基本プロトコル：2026年9月8日」にある
- CodexのPostToolUseは`tool_response`を任意のJSON値として渡す。シェル実行では終了コードを含まず
  出力文字列だけが届くため、コマンドの成否を前提とする状態記録へ使わない。
  `apply_patch`は適用に成功した場合だけ発火するため、編集成功後の状態記録へ利用できる。
  失敗したシェル実行でPostToolUseが発火するかは未検証とする。
  codex-cli 0.153.4で`agents_server`の軽量起動により再現を試みたが、
  当該起動は設定の読込先を空にするため本プラグインのフックが動作せず、状態ファイルが作成されなかった。
  再検証は、通常起動のCodexセッションで失敗するシェル実行を1回行い、
  同じ状態ファイルの`test_executed`と`git_log_checked`の変化を確認する。
  発火の有無によらず終了コードが届かないため、成否を前提とする記録はCodexでは行わない
- Bashコマンドを対象とする検査は、コマンド文字列全体への部分一致で発火させず、
  区間分割とトークン化により対象が実行位置にある場合だけ発火させる
  （検索語・引数として名前が現れるだけの読み取り操作を検出しないため）。
  実行を遮断する検査は、コマンド置換・サブシェル・オプション終端まで解決できる解析を用意できる場合に限り
  同じ判定へ移す。用意できない間は過検出を許容する現行判定を維持する（解析の不足で保護を外さないため）
- 観測した状態に応じて警告又はblockの出力有無を切り替えるフックを計画に含める場合、
  別リポジトリ、別worktree、複数主体の同時実行などの条件が誤って成立する入力と誤って成立しない入力を列挙する。
  各入力の期待動作と検査を計画の実装者向け領域へ記載する
- hook定義が`command`で参照するスクリプトのパスを改名、移動又は削除する場合は、旧パスへ新しい入口を呼び出すだけの互換入口を残す。
  hook定義はセッション起動時に読み込まれ、稼働中のセッションは旧パスを参照し続けるため、実体を失うと当該セッションのツール呼び出しがフック実行の失敗で拒否される。
  hook定義が共通入口へサブコマンドを渡し、共通入口が未知のサブコマンドを終了コード0で通過させる場合は、
  サブコマンドの削除でツール呼び出しが拒否されないため、旧サブコマンド名の実体を残さなくてよい。
  互換入口のdocstringへ役割と撤去条件を書く。撤去は、旧定義を読み込んだセッションが全て終了したことを確認できた場合だけ行い、
  確認できない場合は互換入口を維持する。
  配布物では、新しい入口を含む版を配布した後の版数更新以降を撤去可能な時機の下限とし、版数更新だけを撤去の契機にしない
- イベントごとの入口: 同じイベントへ判定を追加する場合は、新しい登録を並べず当該イベントの既存の入口へ相乗りさせる。
  入口は各判定を順に呼び、判定単位で例外を隔離し、単一の応答へ集約する。
  ホストは登録の件数だけプロセスを起動するため、登録を分けると実行時間と画面の行数が判定の件数に比例して増える。
  matcherを持つ登録を統合する場合は、ツール名による限定を各判定側の早期returnへ移す

## 遮断・警告フックの成立条件

遮断又は警告するフックは、判定に必要な情報がフックの入力（イベントpayload、対象ファイル、セッション状態）から機械的に確定できる場合だけ実装する。
担当の別、所有、作業の収束の有無、会話の意味など、実行主体の判断へ委ねる情報を判定へ要する対象は、遮断も警告もせず規範文書で扱う（厳守規定。判定できない条件で遮断すると、当該条件に無関係な主体が実行できる処置の無い通知を受け取り、ターンを消費する）。
判定を確定できない情報を実行主体へ委ねる前提で通知だけを出力する設計を、本条件の回避経路にしない。
判定条件そのものが実行主体の判断へ依存する場合を本条件の対象とし、通知本文が解消手段として実行主体の判断を求めることは対象外とする。
既存の遮断・警告フックを本条件で点検した結果、条件を満たさないものは、判定条件を機械的に確定できる形へ是正するか、規範文書へ移して当該フックを撤去する。

本条件を満たす判定は、遮断と警告のいずれで返すかを次の基準で決める。
検出した操作に対して規範が定める代替手段が1つに定まり、当該代替手段を同じターンで実行できる場合は遮断とする。
代替手段が複数あり実行主体が選ぶ場合と、検出した操作を続ける判断が成立し得る場合は警告とする。
遮断は当該ターンの操作を失わせるため、実行主体が`fix`の文面どおりに再実行すれば成立する場合に限る。
判定ごとにいずれを選んだかと選んだ根拠を、当該判定モジュールのdocstringへ記録する。

代替手段が複数あるため警告とした判定のうち、同一セッションでの反復が母集団の欠落又は工程の停止を招くものは、1件目を警告、2件目以降を遮断とする。
反復の記録はセッション状態へ検査ごとのキーで残し、遮断の本文へ警告と同じ解消手段を載せる。
本判定は`warn`区分の通知が反復の旨を本文へ含める3件目の閾値とは別のものであり、当該閾値を変更しない。

遮断するフックが、遮断の解除に必要な情報を判定の時点で保持する場合は、当該情報を通知本文へ載せる。
実行主体へ別の呼び出しでの取得を要求すると、遮断のたびに1ラウンドを消費するためである。
通知本文がホストの出力上限を超える見込みがある場合は、載せる対象を上限の内側へ限り、載せなかった対象の取得手順を`fix`へ示す。

`SessionStart`は`agents_server`の委譲先でも発火し、`SubagentStart`は`Agent`ツールのサブエージェントの起動時だけ発火する。
`agent-toolkit/agent_toolkit/_hooks/rules_context.py`は、前者でメイン向け条文を追加するときに委譲先を除く。
判定には環境変数`AGENT_TOOLKIT_DELEGATED_SESSION`と`AGENT_TOOLKIT_OWNER_SESSION`を用い、後者ではサブエージェント向け条文を追加する。

`Stop`と`SubagentStop`へ登録する判定は、いずれも委譲先で発火し得る。
判定が求める処置を委譲先が実行できるかを判定ごとに確定し、結果と根拠を当該判定モジュールのdocstringへ記録する。
`Stop`は当該セッション自身のターン終端で発火するため、`agents_server`が起動した委譲先の判別には環境変数`AGENT_TOOLKIT_DELEGATED_SESSION`を用いる。
`SubagentStop`は`Agent`ツールのサブエージェントのターン終端だけで発火し、入力の`agent_id`が当該サブエージェントを示す。
同環境変数は`agents_server`が起動したセッションを示すに過ぎず、`Agent`ツールのサブエージェントには付かない。
したがって`SubagentStop`の判別へ当該環境変数を用いない。
実行できない処置を求める判定は、`Stop`では当該環境変数、`SubagentStop`では`agent_id`を条件として対象から除く。
最上位セッションだけが実行できる処置は`SubagentStop`へ登録しない。
区分の詳細は`agent-toolkit/rules/99-claude-code.md`の「役割上の区分と実行環境上の区分」を正本とする。

実行を遮断しない`warn`区分の通知は、同一セッション内で同じ`hook_id`と同じ原因の通知が3件目に達した時点から、反復している旨と当該セッションの累積件数を本文へ含める。
原因は通知を生成する検査ごとに一意な識別子で区別し、累積件数はセッション状態ファイルの`warn_notice_counts`が保持する。
3件目を閾値とするのは、2件目までは同種の操作が偶発的に並ぶ範囲であり、3件目以降を実行主体が原因を除去しないまま反復している状態とみなすためである。

## matcher設定

ツール名で`matcher`を評価するイベントは`PreToolUse`、`PostToolUse`、`PostToolUseFailure`、`PermissionRequest`、`PermissionDenied`の5つとする。
これらのイベントの`matcher`は3通りに解釈する。`"*"`、空文字列及びキーの省略は全ツールへ一致する。英数字、`_`、`-`、空白、`,`、`|`だけからなる値は、`|`又は`,`で区切ったツール名の完全一致とする。それ以外の文字を含む値は、先頭と末尾を固定しないJavaScriptの正規表現として評価する。
全ツールへ一致させる登録には`"*"`を書き、空文字列とキーの省略を新規記述へ用いない。ツール名で`matcher`を評価しないイベントには`matcher`キーを置かない。
一次資料は公式ドキュメント<https://code.claude.com/docs/en/hooks.md>の`Matcher patterns`節とする。
監査記録は`docs/development/audit-records.md`の「agent-toolkit/skills/writing-standards/references/claude-hooks.md：matcher設定：2026年9月4日」にある。

- 個別の早期returnガード: `matcher`を広げた場合、hookスクリプト側で`tool_name`を
  確認し対象外を早期returnすることで処理コストと誤検出を抑える
- ホスト間でmatcherを共有しない: Claude Code向けの全ツール一致のmatcherをCodexへそのまま配布すると、
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

Codexのシェル実行は、matcher上で`Bash`に一致する。
統合実行（`exec_command`）も同じく`Bash`に一致する。
入力payloadの`tool_input.command`にはコマンド文字列が入る。
一次資料は<https://learn.chatgpt.com/docs/hooks>のTool coverageとし、`exec`や`shell`をmatcherへ列挙しない。

## 出力フィールドの使い分け

各フィールドのスキーマとイベント別の対応可否は公式ドキュメント
<https://code.claude.com/docs/ja/hooks.md>を一次資料とする。
本節は経路選択の方針だけを定める。

イベントごとの出力契約の機械検査は`agent-toolkit/agent_toolkit/_hooks/output_contract.py`を正本とする。
同ファイルは公式のHooksリファレンスが定める契約をJSON Schemaで保持する。
`agent-toolkit/agent_toolkit/_hooks/output_contract_test.py`が登録済みの全hookの出力を当該契約へ照合する。
フックを追加又は変更する場合は、当該契約と検体を同じ変更単位で更新する。

Claude Codeが表示する`Stop hook error: JSON validation failed`は、プロンプト型hookの評価器が
モデルの応答をJSONとして解析できなかった場合に出る。コマンド型hookのJSON出力の検証経路では出ない。
`/goal`はセッションの範囲で有効なプロンプト型Stop hookを登録する。
`/goal`を設定したセッションでは、当該表示がコマンド型hookの出力形式とは無関係に現れる。
監査記録は`docs/development/audit-records.md`の「agent-toolkit/skills/writing-standards/references/claude-hooks.md：出力フィールドの使い分け：2026年9月4日」にある。

PreToolUse・PostToolUse・UserPromptSubmitでコーディングエージェントに行動を促す場合は`hookSpecificOutput.additionalContext`を第一経路として使う（`_llm_notice`ヘルパー経由の本文構築を推奨）。これらのイベントでは、`additionalContext`はターン継続を強制しない。
`systemMessage`は使わず、stderr出力は`exit 2`のblockと組み合わせる場合のみに限定する。
`systemMessage`の情報通知はユーザーの判断・操作に影響する事象に限って使い、決定論的で失敗しない自動補正の発動など、反復発動してユーザーの対応を要しない事象には付けない。
Stop/SubagentStopで当該ターン継続を強制する用途は、エラーとして遮断する場合（振り返り誘導等）に`decision: "block"`＋`reason`を、フックの想定内の助言に`hookSpecificOutput.additionalContext`を採用する。
永続ログはstderr出力ではなく`_hooks.stop_gate.append_stop_log`等の専用APIに集約する。

| フィールド | 表示先 | 用途 |
| --- | --- | --- |
| `hookSpecificOutput.additionalContext` | コーディングエージェント | AWIを渡す主経路。PreToolUse・PostToolUse・UserPromptSubmitでは継続を強制せず、Stop/SubagentStopでは継続を強制する |
| `reason` | コーディングエージェント（`decision: "block"`時のみ） | blockを併用する場合の理由欄 |
| `permissionDecisionReason` | deny時はコーディングエージェント、allow/ask時はユーザーのみ | PreToolUseの決定理由 |
| `systemMessage`・`stopReason` | ユーザーのみ | 情報通知と`continue: false`時の終了メッセージ |
| `decision.*` | PermissionRequest専用 | 許可・拒否の決定。`hookSpecificOutput`直下に置く |

`decision: "block"`の挙動はイベント別に異なる。
Stop/SubagentStopでは停止を防いでターン継続を強制し、PostToolUseではblock理由を直前のツール結果に添えて返す。
PreToolUse・PostToolUse・UserPromptSubmitで挙動の強制が不要であれば`additionalContext`単独で出力する。Stop/SubagentStopでは`additionalContext`単独でもターン継続を強制する。

- block通知は`_hooks.notice`のblock専用整形関数（`block_formatter`）で生成し、解消手段の`fix`を渡す。`fix`が空文字列または空白文字だけの場合は`ValueError`となる
- 独自の整形関数でblock本文を構成しない（解消手段の欠落を機械的に検出できなくなるため）
- PreToolUse・PostToolUseのblockは当該操作の中止で場面が解消するため、Stop系の成立条件の規定は適用しない

警告専用のPreToolUse出力は`hookSpecificOutput.additionalContext`だけを返し、`permissionDecision`を省略する。
決定を省略すると通常の権限フローが適用され、警告表示が許可プロンプトを省略しない。

コーディングエージェントの出力を対象とする検査は、適用境界を書き込み先ではなく読み手で定める。
ユーザーが直接読む本文を出力する操作は、ファイルへ書き込まない操作であっても、編集入力と同じ本文検査へ通す。
Claude Codeでは`AskUserQuestion`の質問本文・見出し・選択肢の各欄と`ExitPlanMode`の計画本文が該当する。
委譲先が読む指示は本境界の対象に含めない。

組み込みのdeny / askルールはhookの戻り値に関わらず評価される。
`.claude/`配下への書き込み確認等の組み込みaskルールはPreToolUseの`allow`では上書きできない。
確認ダイアログを抑制したい場合はPermissionRequestイベントで`decision.behavior: "allow"`を返す。

`updatedInput`による入力書き換えは、確認ダイアログの発生自体を抑止しない。
ダイアログを伴う値を拒否する必要がある場合は書き換えでなくブロックで扱う。
`agents_server`では`engine`に応じたバックエンドをMCPサーバーが選択する。承認、ユーザー入力、認証更新及び一覧操作は公開せず、実行中turnの明示的な中断だけをsession単位の`kill`として公開する。
PreToolUseは開始ツール（`start`・`start_explore`）の絶対`cwd`と`send_message`・`kill`の保存済みsessionを検査するだけで、入力の実行権限値を自動補正しない。
`wait`は新しいturnを開始せず既存sessionの現在の状態を返すだけで、誤った作業ディレクトリでの実行を招かないため、PreToolUseの検査対象へ含めず通過させる。
PostToolUseは成功した開始ツール（`start`・`start_explore`）のcwdと、`wait`・`send_message`・`kill`のsession状態を記録する。失敗時は状態を変更せず、既存の開始点用
`PostToolUseFailure` matcherを拡張しない。
旧blocking MCPの入力例 `` `sandbox: danger-full-access` `` は移行説明と保護対象の識別にだけ残し、新経路へ渡さない。

エージェントへ特定の行動・引数を要求するblockを新設する場合は、要求する要件を実行主体が事前に読み得る規範文書（常時ロードのルール、または当該作業で起動されるスキルの本文・参照文書）へ明示する。遮断メッセージだけを要件の初出にしない。
blockと警告の新設時は、対象環境で文書化済みの正式コマンド形（スキル・`AGENTS.md`・タスクランナー定義が指定する起動形）への発動有無を確認し、正当な運用が遮断又は警告される場合は判定条件を見直してから導入する。
通知本文が解消手段として示す操作も同じ確認の対象とし、当該操作の入力が同じ判定条件へ一致しないことを確認する（厳守規定。通知が示した操作自体が同じ通知を返すと、当該通知が正しい操作のたびに反復し、同じ標識を持つ通知全体が判断材料として扱われなくなる）。

block検査は、規範の読み込み漏れや手順の取り違えを実行主体へ通知する目的で設計する。
別ツール経由の書き換えやフック自身への変更など、迂回経路の網羅的な遮断を目的とする検査は新設しない。
block文面には検出した原因と、遮断を解除して続行する承認済みの経路を示す。

### PermissionRequest

確認ダイアログ表示時に発火するイベント。ユーザーに代わって許可 / 拒否を決定するときに使う。
スキーマがPreToolUseと異なり、`hookSpecificOutput`直下に`decision`オブジェクトを置く。
`hookEventName`は`"PermissionRequest"`を指定する。

組み込みdenyルールは`allow`でも上書きできないが、確認ダイアログ（ask相当）はスキップできる。

`Read(*.key)`のようにディレクトリを含まないdeny規則は、gitignore構文に従って任意の深さで一致するため、ディレクトリを走査する読み取りコマンドを一律に確認ダイアログの対象へ変える。
このダイアログは本イベントの`allow`でも抑止できないため、保護する対象は当該ファイルを持つリポジトリの設定へ、走査対象を巻き込まない具体的なパスで書く。

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

ユーザー発話への応答契約を注入するhookは、同一セッションの直前の通常発話からの経過時間を状態として保持し、
閾値以上経過した通常発話にだけ`additionalContext`を返す。
初回の通常発話は注入の対象から除くが、経過時間の基準となる時刻を記録する。
初回を含む通常発話では当該時刻を更新し、ハーネスが挿入した通知及びコマンド起動では記録も注入もしない。
この注入はホストを問わず有効であり、Codex payloadでも同じ`additionalContext`を返す。

## Stop/SubagentStopフックの再帰呼び出し対策

Stopの集約入口は連続blockの上限を管理し、上限へ到達した場合に遮断を打ち切る。
個々の判定は、入力payloadの`stop_hook_active`だけを根拠とする無条件approveを行わない。
上限到達時は、打ち切りの事実と遮断していた判定名をStop判定ログへ記録する。
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

Stop・SubagentStopの`additionalContext`と`decision: "block"`の違いは、`additionalContext`がフックの想定内の助言としてtranscriptへ表示され、フックのエラー通知を伴わない点である。
いずれも`stop_hook_active`と連続継続上限による同じループ保護を通るため、前段の厳守規定を両経路へ等しく適用し、対象主体が同一ターン内の行動で解消できる条件だけを警告と遮断の条件にする。

ターン終了の言語的判定（完了文言・質問・待機表明の判別）をフック側のコードで
正規表現等により行うと誤検知が生じやすい。
コーディングエージェントへの誘導文の先頭に判定基準を事前チェックとして埋め込み、
基準を満たさない場合は誘導内容に従わずターンを終了する設計を推奨する。

`Stop`と`SubagentStop`の共通入力は`session_id`・`prompt_id`・`transcript_path`・`cwd`・`permission_mode`・`effort`・`hook_event_name`である。
両イベントはこれに加えて`stop_hook_active`・`last_assistant_message`・`background_tasks`・`session_crons`を受け取る。
`SubagentStop`はさらに`agent_id`・`agent_type`・`agent_transcript_path`を受け取る。
`SubagentStop`の`background_tasks`と`session_crons`は親セッションの範囲を表す。
`background_tasks`は実行中のタスクを1件ずつ表し、各要素は`id`・`type`・`status`・`description`を持つ。
`type`は`shell`・`subagent`・`monitor`・`workflow`・`teammate`・`cloud session`・`MCP task`のいずれかを取る。
値は当該タスクを生成した機能を示す。
この配列は、セッションが完了した状態と、背景の作業による再開を待って停止している状態とをフックが区別する用途で使う。
`PostToolUse`は背景実行への移行の時点で発火する。当該ジョブの完了時に再発火する旨の記載は公式ドキュメントに無い。
監査記録は`docs/development/audit-records.md`の「agent-toolkit/skills/writing-standards/references/claude-hooks.md：Stop/SubagentStopフックの再帰呼び出し対策：2026年9月4日」にある。
現行版の入力に`background_tasks`が現れない場合は本項を失効させ、当該版の観測として書き直す。

CodexのStopは`decision: "block"`と`reason`で同一ターンを継続し、許可時は空のJSONオブジェクトを返す。
CodexのStopは`hookSpecificOutput`を受理しない。
Codex固有の入力には`model`があり、Stopでは`stop_hook_active`と`last_assistant_message`も受け取る。
Codex rolloutのtranscript形式は安定インターフェースではないため、完了判定や背景作業判定の契約に使わない。
状態欠落時の回復判定など、必要な標識の有無を確認する限定用途でだけruntime別に変換する。

## 環境変数の一覧

配布物完結の環境変数（`AGENT_TOOLKIT_<PURPOSE>`形式）の一覧と用途を示す。

- `AGENT_TOOLKIT_PRIVATE_NOTES`: `atk wi`管理repoのroot（既定`~/private-notes/`）
- `AGENT_TOOLKIT_STOP_GATE_DEBUG`: デバッグ出力
- `AGENT_TOOLKIT_HOOK_PAYLOAD_DUMP`: 受信payloadのダンプ先
- `AGENT_TOOLKIT_RESTART_SPEC`: AWI処理の常駐実行で、次に起動するセッションの指定を
  起動側の処理へ渡す一時ファイルのパス
- `AGENT_TOOLKIT_DELEGATED_SESSION`: 委譲先として起動したセッションであることを示す印。常駐実行の終了保証を最上位セッションへ限定する判定に使う
- `AGENT_TOOLKIT_OWNER_SESSION`: 委譲先が取得又は作成した計画バンドルの所有として記録する、委譲元セッションの識別子。`agents_server`が起動した子だけが持つため、Codex backendの委譲先を含めてメイン向け規範の追加を省く判定にも使う

## メッセージの記述言語

コーディングエージェントに直接渡る出力（`reason` / `additionalContext` / exit 2のstderr）は
日本語で記述する。会話コンテキストへ日本語以外の言語の文が挿入されると、
モデルが以後の発話言語を当該言語へ引きずられるためである。
自動生成であることは次節の標識だけが担う。

hookメッセージ中で原本ファイル（`01-agent.md`・`CLAUDE.md`等）の章名・節名・キーワードを参照する場合は、
原本表記をそのまま引用する。
訳した参照名（例:「日本語」節を`Japanese section`と訳すなど）は
原本の章名変更時に追従漏れの起点となるため使わない。
hookメッセージの目的はコーディングエージェントが参照先を特定できることである。

## コーディングエージェント宛てメッセージの標識

コーディングエージェントに直接渡る出力（`reason` / `additionalContext` / exit 2のstderr）には、
自動生成であることを明示するプレフィックスとサフィックスを付ける。
hookの出力はユーザー発言と同じ形で会話コンテキストに注入されるため、指示として誤認されないよう二重の標識を設ける。

### プレフィックス

`[auto-generated]` または `[auto-generated: <plugin>/<hook>]` を行頭に置く。
プラグイン識別子やフック種別のみの内部名（例: `[agent-toolkit]`）はコーディングエージェントの観点では
「自動生成である」という意味論が伝わらないため使わない。

警告などの種別タグ（例: `[warn]`）は、警告であることを区別したい場合に並置する（任意）。

```text
[auto-generated: agent-toolkit/pretooluse] blocked: ...
[auto-generated: agent-toolkit/pretooluse][warn] detected ...
```

### サフィックス

メッセージ本文の末尾に次の一行を追加する。

```text
（自動生成のhook通知。行動する前に会話コンテキストとの関連性を評価すること。）
```

コーディングエージェントに対して「妥当性を文脈と照らして判断してから行動する」ことを明示する。
`systemMessage` / `stopReason` などコーディングエージェントに届かないフィールドや、
`permissionDecision: "allow"`で追加メッセージを持たない経路には付けない。

### ヘルパー関数

hookスクリプトごとに次のようなヘルパーを持ち、発出箇所から呼び出す（重複実装は許容）。

```python
_MESSAGE_PREFIX = "[auto-generated: myplugin/myhook]"
_MESSAGE_SUFFIX = "（自動生成のhook通知。行動する前に会話コンテキストとの関連性を評価すること。）"


def _llm_notice(body: str) -> str:
    return f"{_MESSAGE_PREFIX} {body} {_MESSAGE_SUFFIX}"
```

## セッション状態ファイル

hook間で情報を共有するセッション状態ファイルの設計、寿命、排他制御及び個別フラグの記録元と利用先は`session-state-and-flags.md`が定める。

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
payload設計は、上記の一次資料が示す仕様から確定する。

- 入出力: stdinに呼び出しペイロードのJSONが渡され、stdoutにホスト別契約の応答JSONを出力する。exit codeは0で正常完了とする
  - stderr経由の表示はexit 2との組合せで使う代替手段
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
- 出力フィールドの併用: deny時の`permissionDecisionReason`と`hookSpecificOutput.additionalContext`はどちらもコーディングエージェントに届く。一方で十分なため、重複表示を避け片方に統一する
- フック追加を計画に含める場合、対象イベントの発火条件を計画の実装者向け領域へ事前明示する。
  例えばPostToolUseはツール成功時のみ発火し、失敗時はPostToolUseFailureが処理する。
  auto modeでのブロック等はPermissionDeniedフックが処理する。
  監査記録は`docs/development/audit-records.md`の「agent-toolkit/skills/writing-standards/references/claude-hooks.md：hookスクリプトの基本プロトコル：2026年9月8日」にある
- CodexのPostToolUseは`tool_response`を任意のJSON値として渡す。シェル実行では終了コードを含まず
  出力文字列だけが届くため、状態記録の条件からコマンドの成否を外す。
  `apply_patch`は適用に成功した場合だけ発火するため、編集成功後の状態記録へ利用できる
- Bashコマンドを対象とするhookの判定は、コマンド文字列全体への部分一致で発火させず、
  区間分割とトークン化により対象が実行位置にある場合だけ発火させる
  （検索語・引数として名前が現れるだけの読み取り操作を検出しないため）。
  実行を遮断するチェックは、コマンド置換・サブシェル・オプション終端まで解決できる解析を用意できる場合に限り
  同じ判定へ移す。用意できない間は過検出を許容する現行判定を維持する（解析の不足で保護を外さないため）
- 観測した状態に応じて警告又はblockの出力有無を切り替えるフックを計画に含める場合、
  別リポジトリ、別worktree、複数主体の同時実行などの条件が誤って成立する入力と誤って成立しない入力を列挙する。
  各入力の期待動作と検証方法を計画の実装者向け領域へ記載する
- hook定義が`command`で参照するスクリプトのパスを改名、移動又は削除する場合は、旧パスへ新しいエントリーポイントを呼び出すだけの互換スクリプトを残す。
  hook定義はセッション起動時に読み込まれ、稼働中のセッションは旧パスを参照し続けるため、実体を失うとそのセッションのツール呼び出しがフック実行の失敗で拒否される。
  hook定義が共通エントリーポイントへサブコマンドを渡し、共通エントリーポイントが未知のサブコマンドを終了コード0で通過させる場合は、
  サブコマンドの削除でツール呼び出しが拒否されないため、旧サブコマンド名の実体を残さなくてよい。
  互換スクリプトのdocstringへ役割と撤去条件を書く。撤去は旧定義を読み込んだセッションが全て終了したことを確認できた場合だけ行い、
  確認できない場合は互換スクリプトを維持する。
  配布物では、新しいエントリーポイントを含む版を配布した後の版数更新以降を撤去可能な時機の下限とし、撤去の契機は旧定義を読み込んだセッションの全終了の確認とする
- イベントごとのエントリーポイント: 同じイベントへ判定を追加する場合は、新しい登録を並べずそのイベントの既存のエントリーポイントへ相乗りさせる。
  エントリーポイントは各判定を順に呼び、判定単位で例外を隔離し、単一の応答へ集約する。
  ホストは登録の件数だけプロセスを起動するため、登録を分けると実行時間と画面の行数が判定の件数に比例して増える。
  matcherを持つ登録を統合する場合は、ツール名による限定を各判定側の早期returnへ移す

## 遮断・警告フックの成立条件

遮断・警告するフックは、判定に必要な情報がフックの入力（イベントpayload、対象ファイル、セッション状態）から機械的に確定できる場合だけ実装する。
同じ条件を実行基盤か別の共通フックが既にチェックする場合は、新しいチェックを追加せず既存のチェックに任せる。重複したチェックは実行時間と状態を増やし、両者の判定差による誤遮断を生む。
担当の別、所有、作業の収束の有無、会話の意味など、実行主体の判断へ委ねる情報を判定へ要する対象は、遮断も警告もせず規範文書で扱う。判定できない条件で遮断すると、その条件に無関係な主体が実行できる処置の無い通知を受け取り、ターンを消費する。
判定を確定できない情報を実行主体へ委ねる前提で通知を出力する設計も、本条件の適用対象に含める。
判定条件そのものが実行主体の判断へ依存する場合を本条件の対象とし、通知本文が解消手段として実行主体の判断を求めることは対象外とする。
既存の遮断・警告フックを本条件で点検した結果、条件を満たさないものは、判定条件を機械的に確定できる形へ是正するか、規範文書へ移してそのフックを撤去する。

通知本文が指示する処置を特定の主体種別又は作業ツリー種別だけが実行できる場合は、その種別の判定を発火条件へ含める。
主体種別はhook payloadの`agent_id`と委譲先セッションの環境変数、作業ツリー種別はGitへの照会から確定する。
判定を省くと、その処置の権限も所有も持たない主体が通知を受け取り、呼び出し元への差し戻しだけで終わる工程が発火のたびに生じる。
同じ要因で、回答済みUWIの反映を指示する通知とversion bumpを指示する警告が、いずれも処置できない委譲先とレーン用worktreeへ届いた。
判定入力を解決できない場合は、その通知を出力する側へ倒す。

遮断してよいのは、その操作を通すと復元できない結果が残り、かつ実行主体が同じターンで`fix`の文面どおりに再実行して是正できる場合に限る。
それ以外は警告とする。可逆な操作を遮断すると、そのターンの入力と作業が失われたうえで同じ操作の再実行を要し、その損失が発火のたびに生じる。
復元できない結果の代表例は、実行主体のコンテキストへの取り込み、外部への公開、プロセス又はタスクの終了、対象の上書きと削除とする。
ユーザーへ提示する本文そのものを入力とする判定（`AskUserQuestion`の選択肢、`ExitPlanMode`の計画本文など）は、ユーザー自身が読んで指摘できるため警告で返す。
この判定は`PreToolUse`と`PostToolUse`の遮断を対象とし、`Stop`と`SubagentStop`には「Stop/SubagentStopフックの再帰呼び出し対策」が定める条件を適用する。
判定ごとに結論と根拠をその判定モジュールのdocstringへ記録する。

警告とした判定のうち、同一セッションでの反復が母集団の欠落又は工程の停止を招くものは、`warning_formatter`へ`escalate_on_repeat=True`を明示し、1件目を警告、2件目以降を遮断とする。昇格の既定は偽とする。`removable_cause=True`は反復注記に使い、遮断は昇格の明示を要する。後続の編集で是正できる文体などの警告は昇格させない。昇格したblockはPreToolUseの`exit_with`が終了コード2で返し、同時に保留した警告もstderrへ配送する。反復が招く欠落と停止は復元できない結果に当たるためである。

遮断の解除に必要な情報は通知本文へ載せる。別の呼び出しでの取得を要求すると、遮断のたびに1ラウンドを消費する。
`fix`が名指しする手段を利用できない実行主体がある場合は、同じ判定を通過する別の手段を併記し、無い場合は遮断が解除される条件を書く。
受理集合その他の集合を通知本文へ列挙する場合は、判定した対象と対応づく要素だけを載せる。列挙は要素数に比例して実行主体のコンテキストを占める一方、対応づかない要素は次の操作を変えない。

`SessionStart`は`agents_server`の委譲先でも発火し、`SubagentStart`は`Agent`ツールのサブエージェントの起動時だけ発火する。
`agent-toolkit/agent_toolkit/_hooks/rules_context.py`は前者でメイン向け条文を追加するときに委譲先を除く。
判定には環境変数`AGENT_TOOLKIT_DELEGATED_SESSION`と`AGENT_TOOLKIT_OWNER_SESSION`を用い、後者ではサブエージェント向け条文を追加する。

`Stop`と`SubagentStop`へ登録する判定は、いずれも委譲先で発火し得る。
判定が求める処置を委譲先が実行できるかを判定ごとに確定し、結果と根拠をその判定モジュールのdocstringへ記録する。
`Stop`はそのセッション自身のターン終端で発火するため、`agents_server`が起動した委譲先の判別には環境変数`AGENT_TOOLKIT_DELEGATED_SESSION`を用いる。
`SubagentStop`は`Agent`ツールのサブエージェントのターン終端だけで発火し、入力の`agent_id`がそのサブエージェントを示す。
同環境変数は`agents_server`が起動したセッションを示すに過ぎず、`Agent`ツールのサブエージェントには付かない。
したがって`SubagentStop`の判別には`agent_id`を用いる。
実行できない処置を求める判定は、`Stop`ではその環境変数、`SubagentStop`では`agent_id`を条件として対象から除く。
`SubagentStop`へ登録するのは、サブエージェントが実行できる処置に限る。
区分の詳細は`agent-toolkit:delegation`の`references/claude-code-runtime.md`「実行時能力と通信scope」に従う。

実行を遮断しない`warn`区分の通知は、同一セッションで同じ原因の通知が反復した時点から、反復している旨と累積件数を本文へ含める。
閾値と記録先は`agent-toolkit/agent_toolkit/_hooks/notice.py`が持つ。

## matcher設定

ツール名で`matcher`を評価するイベントは`PreToolUse`、`PostToolUse`、`PostToolUseFailure`、`PermissionRequest`、`PermissionDenied`の5つとする。
これらのイベントの`matcher`は3通りに解釈する。`"*"`、空文字列及びキーの省略は全ツールへ一致する。英数字、`_`、`-`、空白、`,`、`|`だけからなる値は、`|`又は`,`で区切ったツール名の完全一致とする。それ以外の文字を含む値は、先頭と末尾を固定しないJavaScriptの正規表現として評価する。
全ツールへ一致させる登録には`"*"`を書き、新規記述で用いる表記をこの1つに限る。ツール名で`matcher`を評価しないイベントでは`matcher`キーを省く。
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

## 出力フィールドの使い分け

各フィールドのスキーマとイベント別の対応可否は公式ドキュメント
<https://code.claude.com/docs/ja/hooks.md>を一次資料とする。
本節は出力先の選択方針だけを定める。

イベントごとの出力契約は`agent-toolkit/agent_toolkit/_hooks/output_contract.py`が定める。
同ファイルは公式のHooksリファレンスが定める契約をJSON Schemaで保持する。
`agent-toolkit/agent_toolkit/_hooks/output_contract_test.py`が登録済みの全hookの出力がその契約に合致するか確かめる。
フックを追加又は変更する場合は、その契約とテストを同じ変更単位で更新する。

Claude Codeが表示する`Stop hook error: JSON validation failed`はプロンプト型hookの評価器が
モデルの応答をJSONとして解析できなかった場合に出る。この表示が出るのはプロンプト型hookの評価器に限る。
`/goal`はセッションの範囲で有効なプロンプト型Stop hookを登録する。
`/goal`を設定したセッションでは、その表示がコマンド型hookの出力形式とは無関係に現れる。
監査記録は`docs/development/audit-records.md`の「agent-toolkit/skills/writing-standards/references/claude-hooks.md：出力フィールドの使い分け：2026年9月4日」にある。

PreToolUse・PostToolUse・UserPromptSubmitでコーディングエージェントに行動を促す場合は`hookSpecificOutput.additionalContext`を第一の通知手段として使う（`_llm_notice`ヘルパー経由の本文構築を推奨）。これらのイベントでは、`additionalContext`はターン継続を強制しない。
`systemMessage`は使わず、stderr出力は`exit 2`のblockと組み合わせる場合のみに限定する。
`systemMessage`の情報通知はユーザーの判断・操作に影響する事象に限って使う。決定論的で失敗しない自動補正の発動など、反復発動してユーザーの対応を要しない事象は対象の外に置く。
Stop/SubagentStopでそのターン継続を強制する用途は、エラーとして遮断する場合（振り返り誘導等）に`decision: "block"`＋`reason`を、フックの想定内の助言に`hookSpecificOutput.additionalContext`を採用する。
永続ログはstderr出力ではなく`_hooks.stop_gate.append_stop_log`等の専用APIに集約する。

| フィールド | 表示先 | 用途 |
| --- | --- | --- |
| `hookSpecificOutput.additionalContext` | コーディングエージェント | AWIを渡す主要な通知手段。PreToolUse・PostToolUse・UserPromptSubmitでは継続を強制せず、Stop/SubagentStopでは継続を強制する |
| `reason` | コーディングエージェント（`decision: "block"`時のみ） | blockを併用する場合の理由欄 |
| `permissionDecisionReason` | deny時はコーディングエージェント、allow/ask時はユーザーのみ | PreToolUseの決定理由 |
| `systemMessage`・`stopReason` | ユーザーのみ | 情報通知と`continue: false`時の終了メッセージ |
| `decision.*` | PermissionRequest専用 | 許可・拒否の決定。`hookSpecificOutput`直下に置く |

`decision: "block"`の挙動はイベント別に異なる。
Stop/SubagentStopでは停止を防いでターン継続を強制し、PostToolUseではblock理由を直前のツール結果に添えて返す。
PreToolUse・PostToolUse・UserPromptSubmitで挙動の強制が不要であれば`additionalContext`単独で出力する。Stop/SubagentStopでは`additionalContext`単独でもターン継続を強制する。

- block通知は`_hooks.notice`のblock専用整形関数（`block_formatter`）で生成し、解消手段の`fix`を渡す。`fix`が空文字列または空白文字だけの場合は`ValueError`となる
- block本文の構成はこの整形関数に限る（独自の整形関数では解消手段の欠落を機械的に検出できなくなるため）
- PreToolUse・PostToolUseのblockはその操作の中止で場面が解消するため、Stop系の成立条件の規定は適用しない

警告専用のPreToolUse出力は`hookSpecificOutput.additionalContext`だけを返し、`permissionDecision`を省略する。
決定を省略すると通常の権限フローが適用され、警告表示とは独立に許可プロンプトが出る。

コーディングエージェントの出力を対象とするチェックは、適用境界を書き込み先ではなく読み手で定める。
ユーザーが直接読む本文を出力する操作は、ファイルへ書き込まない操作であっても、編集入力と同じ本文チェックへ通す。
Claude Codeでは`AskUserQuestion`の質問本文・見出し・選択肢の各欄と`ExitPlanMode`の計画本文が該当する。
本境界の対象は、ユーザーが直接読む本文に限る。

組み込みのdeny / askルールはhookの戻り値に関わらず評価される。
`.claude/`配下への書き込み確認等の組み込みaskルールはPreToolUseの`allow`では上書きできない。
確認ダイアログを抑制したい場合はPermissionRequestイベントで`decision.behavior: "allow"`を返す。

`updatedInput`による入力書き換えの効果は入力値の変更までとし、確認ダイアログの発生はそのまま残る。
ダイアログを伴う値を拒否する必要がある場合は書き換えでなくブロックで扱う。
`agents_server`では`engine`に応じたバックエンドをMCPサーバーが選択する。承認、ユーザー入力、認証更新及び一覧操作は公開せず、実行中turnの明示的な中断だけをsession単位の`kill`として公開する。
PreToolUseの処理は、`send_message`・`kill`の保存済みsessionのチェックまでとし、開始ツールの入力妥当性検証は実行基盤へ委ねる。入力の実行権限値はそのまま渡す。
`wait`は新しいturnを開始せず既存sessionの現在の状態を返すだけで、誤った作業ディレクトリでの実行を招かないため、PreToolUseのチェック対象へ含めず通過させる。
PostToolUseは成功した開始ツール（`start`・`start_explore`）のcwdと、`wait`・`send_message`・`kill`のsession状態を記録する。
旧blocking MCPの入力例 `` `sandbox: danger-full-access` `` の用途は移行説明と保護対象の識別に限る。

エージェントへ特定の行動・引数を要求するblock又はwarnは、要求する要件を実行主体が発火前に読み得る規範文書（常時ロードのルール、またはその作業で起動されるスキルの本文・参照文書）へ明示する。要件の初出は、その規範文書側に置く。
全てのblockとwarnは、対象環境で文書化済みの正式コマンド形（スキル・`AGENTS.md`・タスクランナー定義が指定する起動形）への発動有無を確認し、規範どおりの通常操作では発火しない判定条件にする。
通知本文が解消手段として示す操作も同じ確認の対象とし、その操作の入力が同じ判定条件へ一致しないことを確認する。通知が示した操作自体が同じ通知を返すと、その通知が正しい操作のたびに反復し、同じ標識を持つ通知全体が判断材料として扱われなくなる。
Claude CodeとCodexの双方に対応するフックでは、ホスト固有の入力・終了状態が必要な判定だけを差分とし、共有できる判定条件と規範参照を共通実装へ置く。

blockするチェックは規範の読み込み不足や手順の取り違えを実行主体へ通知する目的で設計する。
新設するチェックの目的はこの通知に限る。別ツール経由の書き換えやフック自身への変更など、迂回手段の網羅的な遮断は目的の外に置く。
block文面には検出した原因と、遮断を解除して続行する承認済みの手順を示す。

### PermissionRequest

確認ダイアログ表示時に発火するイベント。ユーザーに代わって許可 / 拒否を決定するときに使う。
スキーマがPreToolUseと異なり、`hookSpecificOutput`直下に`decision`オブジェクトを置く。
`hookEventName`は`"PermissionRequest"`を指定する。

組み込みdenyルールは`allow`でも上書きできないが、確認ダイアログ（ask相当）はスキップできる。

`Read(*.key)`のようにディレクトリを含まないdeny規則は、gitignore構文に従って任意の深さで一致するため、ディレクトリを走査する読み取りコマンドを一律に確認ダイアログの対象へ変える。
このダイアログは本イベントの`allow`でも抑止できないため、保護する対象はそのファイルを持つリポジトリの設定へ、走査対象を巻き込まない具体的なパスで書く。

`matcher`はツール名で評価する（`Bash` / `Edit|Write`等）。
入力payloadは`tool_name` / `tool_input`に加え、`permission_suggestions`配列を受け取る。

### UserPromptSubmit

`hookSpecificOutput.additionalContext`をそのターンの応答生成前にユーザー発話へ前置注入する誘導に使う。
本イベントが受理する出力は`hookSpecificOutput.additionalContext`までとする。
Claude Codeでは`decision`と独立に注入可能で、stdoutプレーン出力もコンテキストへ追加される。
Codexでは`hookSpecificOutput.hookEventName`を`UserPromptSubmit`とし、`additionalContext`を返す。

Claude CodeのUserPromptSubmit payloadから現在のセッション名を取得する入力値は得られない。
計画ファイルを扱うhookは、同一セッションでまだ出力していない場合だけ計画ファイル名のstemを
`sessionTitle`へ一度だけ出力する。
`sessionTitle`と`additionalContext`が同じ呼び出しで必要な場合は、`hookSpecificOutput`へ両方を含む1つのJSONを返す。
この契約はClaude Code専用とし、`sessionTitle`の出力先をClaude Code payloadに限る。

ユーザー発話への応答契約を注入するhookは、注記の本文の種別で注入の頻度を分ける。

- 規範文書の読込だけを求める注記は、通常発話の受領ごとに返す。本文へ判定手順を書かず、その要件を定義する基準文書の所在だけを示す。
  ユーザーの介入のたびにその規範の所在を想起させるため、経過時間によらず毎回返す。
  本文を1文へ抑えることで、受領ごとに文脈へ載る量を一定に保つ
- 判定手順を本文へ持つ注記は、同一セッションの直前の通常発話からの経過時間を状態として保持し、閾値以上経過した通常発話にだけ返す。
  初回の通常発話はその注記の対象から除くが、経過時間の基準となる時刻を記録する

初回を含む通常発話ではその時刻を更新する。注記の記録と注入の対象は通常発話に限り、ハーネスが挿入した通知及びコマンド起動は対象の外に置く。
成立した注記が複数ある場合は、1つの`additionalContext`へ結合して返す。
この注入はホストを問わず有効であり、Codex payloadでも同じ`additionalContext`を返す。

## Stop/SubagentStopフックの再帰呼び出し対策

Stopの集約エントリーポイントは連続blockの上限を管理し、上限へ到達した場合に遮断を打ち切る。
個々の判定は、入力payloadの`stop_hook_active`に加えて自身の判定条件で継続の可否を決める。
上限到達時は、打ち切りの事実と遮断していた判定名をStop判定ログへ記録する。
`stop_hook_active`は直前の同フック呼び出しがそのターンの終了を一度阻止したことを示す。

Stop/SubagentStopの`decision: "block"`は対象主体が同一ターン内の行動で解消できる条件に限る。
外部事象の完了、他主体の稼働状態、直前ターンで確定済みの内容など、対象主体の行動で変えられない状態は条件の外に置く。
待機中の主体が終了を拒否されると、ターンを終える以外の動作が残らず、無操作のツール呼び出しの反復に陥る。

この対策を要する理由は次のとおりである。
フックがターン終了を阻止するとコーディングエージェントは新たな応答を生成し、
その応答に対して同じフックが再び発火する。
判定条件が変化しない場合、この繰り返しはClaude Codeの連続block上限まで続く。
上限に達すると警告とともにフックの判定が無視されてターンが終了する。
上限値は`CLAUDE_CODE_STOP_HOOK_BLOCK_CAP`環境変数で変更できる。

Stop・SubagentStopの`additionalContext`と`decision: "block"`の違いは`additionalContext`がフックの想定内の助言としてtranscriptへ表示され、フックのエラー通知を伴わない点である。
いずれも`stop_hook_active`と連続継続上限による同じループ保護を通るため、前段の原則として従う規定を両通知形式へ等しく適用し、対象主体が同一ターン内の行動で解消できる条件だけを警告と遮断の条件にする。

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
値はそのタスクを生成した機能を示す。
この配列はセッションが完了した状態と、背景の作業による再開を待って停止している状態とをフックが区別する用途で使う。
`PostToolUse`は背景実行への移行の時点で発火する。そのジョブの完了時に再発火する旨の記載は公式ドキュメントに無い。
監査記録は`docs/development/audit-records.md`にある。
該当する見出しは「Stop/SubagentStopフックの再帰呼び出し対策：2026年9月4日」である。
現行版の入力に`background_tasks`が現れない場合は本項を失効させ、その版の観測として書き直す。

CodexのStopは`decision: "block"`と`reason`で同一ターンを継続し、許可時は空のJSONオブジェクトを返す。
CodexのStopは`hookSpecificOutput`を受理しない。
Codex固有の入力には`model`があり、Stopでは`stop_hook_active`と`last_assistant_message`も受け取る。
Codex rolloutのtranscript形式は安定インターフェースではないため、完了判定や背景作業判定の取り決めの入力の外に置く。
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
モデルが以後の発話言語をその言語へ引きずられるためである。
自動生成であることは次節の標識だけが担う。

hookメッセージ中で原本ファイル（`01-agent.md`・`CLAUDE.md`等）の章名・節名・キーワードを参照する場合は、
原本表記をそのまま引用する。
訳した参照名（例:「日本語」節を`Japanese section`と訳すなど）は
原本の章名を変更したときに参照が追従しなくなるため、参照名は原本表記のまま用いる。
hookメッセージの目的はコーディングエージェントが参照先を特定できることである。

## コーディングエージェント宛てメッセージの標識

コーディングエージェントに直接渡る出力（`reason` / `additionalContext` / exit 2のstderr）は、
`agent-toolkit-auto-inserted`要素で全体を囲む。`source`へ`<plugin>/<hook>`、`kind`へ通知種別を置く。
hookの出力はユーザー発言と同じ形で会話コンテキストに注入されるため、機械判定できる境界と出所を設ける。

種別は受領した主体が通知の原因を除去できるかで選ぶ。除去できる事象には`warn`、発話ごとの定型の配送には`notice`、遮断には`block`を使う。
振り返りの証拠抽出器は`info`又は`notice`を持つhook通知を問題候補から除く。原因も対策も持たない通知へ`warn`を指定すると、候補の判定工程が発話のたびに生じる。

```xml
<agent-toolkit-auto-inserted source="agent-toolkit/pretooluse" kind="warn">
detected ...
</agent-toolkit-auto-inserted>
```

`systemMessage` / `stopReason` などコーディングエージェントに届かないフィールドや、
`permissionDecision: "allow"`で追加メッセージを持たない呼び出しは、付与の対象の外に置く。

### hook以外で生成する本文の要素

自動生成する本文はhook以外の生成元も`agent-toolkit-auto-inserted`で囲む。`source`と`kind`で生成主体と用途を区別し、agent間の配送では`from`と`composed-by`も残す。最初の開始タグと最後の同名終了タグで境界を確定する。

| 要素 | 対象の本文 |
| --- | --- |
| `agent-toolkit-auto-inserted` | hook通知、規範、agent間配送、機械生成の入力 |
| `forwarded-user-input` | 自動生成本文の中に保持したユーザー自身の入力 |

機械が生成した本文は、ユーザー発話の解釈規範を再読させる注記の対象から外れる。
スラッシュコマンドはホストが1行目の先頭でだけ解釈するため、コマンドを伴う本文では引数の位置へ標識を置く。

### ヘルパー関数

共有formatterを発出箇所から呼び出す。hookごとの重複実装は置かない。

```python
from agent_toolkit._hooks.notice import formatter


_llm_notice = formatter("myplugin/myhook")
```

## セッション状態ファイル

hook間で情報を共有するセッション状態ファイルの設計、寿命、排他制御及び個別フラグの記録元と利用先は`session-state-and-flags.md`が定める。

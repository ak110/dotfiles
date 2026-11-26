# Claude Code・Codex Hook実装ガイドライン

フックを作る担当がホストの入出力に合わせ、ユーザーとエージェントのそれぞれに次の判断に必要な通知を届ける。
Claude CodeとCodexのhookの入出力、matcher、出力欄、`Stop`・`SubagentStop`、管理環境での実行、子のClaude Codeへの環境変数、auto modeのカスタムルールを扱う。
hookを新設・変更する時と、ホストの機能の可否や入出力を調べる時に使う。
ホストの仕様は版で変わるため、原則3（`agent-basics:writing-standards/references/quality-principles-facts.md`）に従って入出力を公式資料と観測で確定し、Claude Code固有の上限値と出力契約にはホスト名を書く。
遮断と警告の判断、エージェント宛ての通知本文は`agent-basics:writing-standards/references/claude-hooks-block-warn.md`が扱う。

## 公式リファレンス（Claude Code）

一次資料は、公式マーケットプレイス（`anthropics/claude-plugins-official`）の`skill-creator:skill-creator`と`plugin-dev`のスキル、Claude Codeの公式ドキュメント、Codexの<https://learn.chatgpt.com/docs/hooks>である。
両プラグインは有効化しなくても`~/.claude/plugins/marketplaces/claude-plugins-official/plugins/<プラグイン名>/`から読め、そこに無い仕様と手元の認識と違う点は公式ドキュメントで確かめる。
公式ドキュメントは`https://code.claude.com/docs/ja/`の各ページ（`hooks.md`、`skills.md`など）で、他のページは索引`https://code.claude.com/docs/llms.txt`の英語版URLの`/en/`を`/ja/`に替えて読む。

## モデル別の公式プロンプトガイド

モデルの更新などで指示文を点検する時は、対象のモデル・ホスト・版に合う提供元の公式ガイドを選び、モデル固有の推奨と対象環境で観測した効果を分ける。

- Claude: [Prompting best practices](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices)と、同サイトのモデル別ガイド（[Opus 5.5](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-opus-5-5)、[Fable 5.2](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-fable-5-2)など。effort、完遂の指示、承認規則、委譲を扱う）を読む。
- OpenAI: [Prompt engineering](https://platform.openai.com/docs/guides/prompt-engineering)から使うモデルのガイドと推論設定の資料へ進む。
  [GPT-6.1 Sol](https://developers.openai.com/api/docs/models/gpt-6.1-sol)が案内する[Using GPT-6](https://developers.openai.com/api/docs/guides/latest-model#gpt-61-sol)はGPT-6 Astraでの観測に基づくため、Sol固有の仕様と共通の指針を分け、効果は使うモデルと課題で確かめる。

## hookスクリプトの基本プロトコル

- 入出力: 標準入力から呼び出しのJSONを受け取り、標準出力へホストの仕様に沿う応答のJSONを出し、正常終了は終了コード0とする。
- 失敗時の扱い（Claude Code v2.1.295以降のcommand・HTTP hook）: 判定を返す前の失敗で操作を続けるか止めるかを、`onFailure`（指定なしは`"continue"`、止める時は`"block"`）で指定する。
  失敗は、起動不能、タイムアウト、0と2以外の終了コード、JSONの解析・検証の失敗、HTTPの接続エラーと2xx以外の応答など、ホストが検知した障害である。
  正常な判定による遮断・警告とは別の設定であり、`"block"`は動作不良の間も操作を止めるべき場合に選ぶ。
  hookが例外を捕捉して終了コード0で終えるとホストは失敗を検知できず、`onFailure`の遮断も働かなくなる。
  `Stop`、`SubagentStop`、`TaskCompleted`、`TeammateIdle`と、`async`・`asyncRewake`の背景のcommand hookでは無効である（[Block the action when a hook fails](https://code.claude.com/docs/en/hooks#block-the-action-when-a-hook-fails)）。
- `${CLAUDE_PLUGIN_ROOT}`: Claude Codeは現在のプラグインのルートへ置換し、Codexが置換するのはプラグインhookの`command`の中だけである。
  スキル本文から実行するコマンドは、読み込んだSKILL.mdの絶対パスから特定したプラグインルートを使う。
- Codexの信頼確認: プラグイン同梱のhookも、定義を変えた後は`/hooks`で内容を確かめて信頼を設定する（それまでCodexはそのhookを実行しない状態が続く）。
- 呼び出し主体の判別: サブエージェントとメインは共通入力の`agent_id`で区別する。
  `transcript_path`はサブエージェント内の発火でもメインの記録を指すため、サブエージェント自身の記録は`SubagentStop`の`agent_transcript_path`で読む。
- 同一ターンの本文の可視性: `PreToolUse`の発火時点では、同じターンのテキストブロックは`transcript_path`のJSONLにまだ無いため、同一ターンの本文で判定する処理は他のイベントに置く。
  思考ブロックやツール呼び出しだけのターンも記録されるため、直近の本文を調べる判定はテキストブロックの無いターンを除いて遡る。
- 発火条件: `PostToolUse`はツールの実行が成功した時だけ発火する。
  `PostToolUseFailure`は実行を始めたツールの失敗で発火して`additionalContext`を受け付け、入力検証による拒否と実行前の権限拒否は対象外である。
  `PermissionDenied`はauto modeの拒否だけで発火し（deny規則への一致、手動の拒否、`PreToolUse`の遮断は対象外）、出力は`hookSpecificOutput.retry`だけを受け付けるため回復の案内は別の手段で届ける。
- Codexの`PostToolUse`: `tool_response`は任意のJSON値で、シェル実行では終了コードを含まず出力文字列だけが渡るため、状態を記録する条件からコマンドの成否を外す。
  `apply_patch`はパッチの適用が成功した時だけ発火するため、編集の成功後の状態記録に使える。
- 起動パスの互換性維持: hook定義の`command`が指すスクリプトを改名・移動・削除する時は、旧パスに新しいエントリーポイントを呼ぶ互換スクリプトを残す（実行中のセッションは起動時の旧パスを使い続け、実体が無いとツール呼び出しが拒否される）。
  未知のサブコマンドを終了コード0で通す共通のエントリーポイントなら、サブコマンドの削除に互換スクリプトは不要である。
  互換スクリプトの役割と撤去条件を追跡できるようにし、旧定義を読んだセッションが全て終わってから撤去する（配布物では、新しいエントリーポイントを含む版のリリース後の版の更新を最低の基準とする）。
- イベントごとのエントリーポイント集約: 同じイベントの判定は既存のエントリーポイントへ統合する（努力目標。登録の数だけプロセスが起動し、実行時間と画面のログ行が増える）。
  判定ごとに例外を隔離して1つの応答JSONへまとめ、`matcher`による対象の限定は各判定の早期リターンで行う。

## matcher設定

ツール名で`matcher`を評価するイベントは、`PreToolUse`、`PostToolUse`、`PostToolUseFailure`、`PermissionRequest`、`PermissionDenied`の5つである。
`"*"`、空文字列、キーの省略は全てのツールに一致する。
英数字と`_`、`-`、空白、`,`、`|`だけの文字列は`|`か`,`で区切ったツール名の完全一致として評価する。
それ以外の文字を含む文字列は、先頭と末尾を固定しないJavaScriptの正規表現として評価する（公式の`Matcher patterns`節）。
全ツールに一致させる新規登録は`"*"`を明記し（努力目標）、ツール名で評価しないイベントでは`matcher`キーを省く。
対象を広くした時は、スクリプトで`tool_name`を確かめて対象外を早期リターンする。
Codexには、ツール名を明示して対象を限定したmatcherを渡す（Claude Code向けの全ツール一致では、入力仕様を確かめていないツールでもハンドラーが起動する）。

## Codexの編集ツール入力

Codexの`apply_patch`はmatcherでは`Edit`・`Write`の別名として一致するが、入力の`tool_name`は`apply_patch`のままである。
変更内容は`tool_input.command`に`*** Begin Patch`から`*** End Patch`までの構文で渡り、1回の呼び出しに複数ファイルの`Add File`、`Update File`、`Delete File`、`Move to`と`@@`区切りの差分が入る。
ホスト間の差を検査の本体へ持ち込まないよう、編集の入力は、入力だけから操作の種別・対象パス・順序付きの編集断片を取り出す操作記録（`PostToolUse`でも使える）と、それを現在の内容へ当てた変更前後像（`PreToolUse`だけで使う）の2層に正規化する。
相対パスは入力の`cwd`を起点に解決し、パッチ構文を解釈できない時はhookで遮断せず、妥当性の判定を`apply_patch`の本体に任せる。
ホストの判定は、Codexがターン単位のhookに付ける空でない文字列の`turn_id`と、`UserPromptSubmit`でCodexだけが渡す`prompt`と`model`の組で行い、ツール名からの推測を避ける。
複数のファイルや検査の警告は1つの`hookSpecificOutput.additionalContext`へまとめる（標準出力の全体が1つのJSONとして解析されるため）。
Codexのシェル実行と統合実行（`exec_command`）はmatcherで`Bash`に一致して`tool_input.command`にコマンド文字列が渡るため、matcherの名前は`Bash`に限る（公式のTool coverage）。

## 出力フィールドの使い分け

イベントごとの出力契約は公式リファレンスからスキーマへ写して登録したhookの応答を検証し、hookを追加・変更する時は出力契約と検証を同じ変更で直す。
行動を促す通知は`hookSpecificOutput.additionalContext`を主に使い、挙動の強制が要らない`PreToolUse`・`PostToolUse`・`UserPromptSubmit`ではそれだけを出す。
標準エラー出力は、終了コード2の遮断と組み合わせる時だけ使う。
`systemMessage`は、ユーザーの判断や操作に関わる重要な情報と、エージェントに操作や是正を求める通知のユーザー向けの写しに使う。
ユーザーの対応が要らない定常の事象（失敗しない自動補正の発動など）と、測定値や規範本文の配送のように情報を渡すだけの通知は、画面を埋めるため写しの対象外とする。
写しはエージェント宛ての欄の内容と配送を元のまま保ち、生成元と種別が分かるよう同じ標識を付ける。
`Stop`・`SubagentStop`で継続を強制する時は、エラーに当たる遮断（振り返りの誘導など）に`decision: "block"`と`reason`を、想定内の助言に`additionalContext`を使う。

| フィールド | 表示先 | 用途 |
| --- | --- | --- |
| `hookSpecificOutput.additionalContext` | エージェント | 行動を促す主な通知。`PreToolUse`・`PostToolUse`・`UserPromptSubmit`では継続を強制せず、`Stop`・`SubagentStop`では強制する |
| `hookSpecificOutput.updatedToolOutput` | エージェント | `PostToolUse`でモデルへ渡すツール結果の置き換え。同じイベントの複数のhookは元の出力に対して並行に動き、最後に返った置き換えが採られる |
| `reason` | エージェント（`decision: "block"`の時だけ） | 遮断の理由 |
| `permissionDecisionReason` | denyではエージェント、allow・askではユーザーだけ | `PreToolUse`の決定の理由。denyでは`additionalContext`と片方にまとめる（努力目標） |
| `systemMessage`・`stopReason` | ユーザーだけ | 情報の通知、是正を求める通知の写し、`continue: false`の時の終了メッセージ |
| `decision.*` | `PermissionRequest`専用 | 許可・拒否の決定。`hookSpecificOutput`の直下に置く |

`decision: "block"`は、`Stop`・`SubagentStop`では停止を防いで継続を強制し、`PostToolUse`では直前のツール結果に理由を添えて返す。
警告だけの`PreToolUse`は`additionalContext`だけを返して`permissionDecision`を省き、通常の権限判定に任せる。
`PreToolUse`の`additionalContext`はその呼び出しの実行を止めず、エージェントは呼び出しの結果と同時に読むため、作用するのは次の操作からである。
その呼び出しを別の手段へ替えさせるには、`permissionDecision: "deny"`で拒否する。
`Stop hook error: JSON validation failed`は、プロンプト型hookの評価器がモデルの応答をJSONとして解析できなかった時に出る。

### 出力上限

公式の[JSON output](https://code.claude.com/docs/en/hooks#json-output)は、`additionalContext`、`systemMessage`、`initialUserMessage`の各文字列と通常の標準出力に10,000文字の上限を定める。
JSONでは欄の値ごと、通常の標準出力では全体で数え、同じイベントの複数のhookも個別に数える。
超えた全文はセッション内の一時ファイルへ保存されてパスと先頭最大2,000文字のプレビューに置き換わり、モデルへ読取は促されないため、確実に届ける情報は上限内に収める。
`PreToolUse`が終了コード2で返す標準エラー出力はこの上限の対象外だが、無制限とはみなさない。

## PermissionRequest

確認ダイアログの表示時に発火し、ユーザーに代わってツールの実行の許可・拒否を決める。
`PreToolUse`と違い、`decision`オブジェクトを`hookSpecificOutput`の直下に置き、`hookEventName`は`"PermissionRequest"`とする。
組み込みのdeny・askルールはhookの戻り値によらず評価され、denyは`allow`を返しても適用される。
組み込みのaskルール（`.claude/`配下への書込の確認など）は、`PreToolUse`の`allow`では上書きできず、`PermissionRequest`の`decision.behavior: "allow"`ならダイアログを抑えられる。
`updatedInput`は入力を変えるだけでダイアログは残るため、ダイアログが出る入力を拒否したい時は遮断として扱う。
`Read`のdeny規則にディレクトリを含まないglobを使うとgitignoreの構文で任意の深さに一致し、ディレクトリを走査する読取コマンドが一律に確認の対象になる。
このダイアログは`PermissionRequest`の`allow`でも抑えられないため、保護するファイルはリポジトリの設定に具体的なパスで書く。
`matcher`はツール名（`Bash`、`Edit|Write`など）で評価し、入力には`tool_name`、`tool_input`と`permission_suggestions`の配列が入る。

## UserPromptSubmit

エージェントがそのターンの応答を作る前に、`hookSpecificOutput.additionalContext`でユーザー発話の前に文脈を差し込む。
Claude Codeでは`decision`と独立に差し込め、標準出力へ書いたテキストも文脈に加わる。
Codexでは`hookSpecificOutput.hookEventName`に`UserPromptSubmit`を指定して`additionalContext`を返す。

## 組織向け設定

Claude Code 2.1.304以降で`cc-plugin-sec-default`が有効な管理環境（Team・Enterpriseなど）では、ユーザーが導入したプラグインの一部のFunction hooksの実行が省略される。
組織のmodとして認識される条件は、[公式の組織向け設定](https://code.claude.com/docs/en/plugins/mods/admin#install-your-organizations-mods-and-set-the-order)で版ごとに確かめる。
2.1.324以降のremote marketplaceでは、管理設定の`enabledPlugins`・`extraKnownMarketplaces`と、`prependPlugins`か`appendPlugins`を組み合わせる。
マーケットプレイスの許可リストは導入元を制限する設定であり、組織のmodの登録は前記の管理設定で行う。
全ての配布先で実行する必要がある処理は、`hooks.json`のコマンドhookか、次の省略対象の外のイベントに置く。

- `classic.`で始まるイベント
- `prompt.section`、`prompt.context`、`prompt.attachment`、`prompt.compose`、`skill.prompt`、`attribution.text`、`session.compact`、`settings.read`
- doorが`attachment`、`hook-context`、`compaction`の`session.append`

ガードの通知はイベント単位であり、省略対象外のdoorに限った`session.append`でも通知される。
任意の最適化だけを理由に登録すると、組織の環境では最適化が動かず毎セッションの省略通知も残るため、最適化は任意の処理として扱い組織側の制約を保つ。
省略される登録を保つ要件がある時は、理由、通知の有無、代替で保証できる範囲を個別に記録する。
コマンドの`PreCompact`は短い要約への指示を渡すが、要約モデルの呼び出しは残る。
背景Bashの完了通知を`session.receive`で取得できない版では、`queue`と`delivery`を受け取る`session.append`を保ち、省略通知は組織のmodの登録で解消する。

## Stop/SubagentStopフック

### 遮断の条件と再帰呼び出し対策

遮断と警告の使い分けは`agent-basics:writing-standards/references/claude-hooks-block-warn.md`「遮断と警告の選択」に従う。
`Stop`の遮断は、常駐運用の完了保証のようにセッション自身がその場で機械的に解決できる条件に限り、作業の完遂度や質問の意図など判断を要する条件は文書の記述で導く。
hookが終了を止めると新しい応答で同じhookが再び発火し、条件が解消しないと連続blockの上限（環境変数`CLAUDE_CODE_STOP_HOOK_BLOCK_CAP`）まで繰り返した後、判定が無視されてターンが終わる。

- `Stop`のhookを集めるエントリーポイントで連続blockを数えて上限で遮断を打ち切り、その事実と判定名を判定のログへ残す。
  各判定は、同じターンで直前の呼び出しが一度終了を止めたかを示す`stop_hook_active`と自身の条件で、継続できるかを決める。
- `decision: "block"`も`additionalContext`も、対象エージェントが同じターンの行動で自力で解決できる条件に限る（1回限りの遮断でも同じ）。
  外部イベントの完了待ち、他のエージェントの稼働状態、前のターンで確定した内容は条件から除く（終了を拒まれた待機中のエージェントは、何もしないツール呼び出しを繰り返す）。
- `additionalContext`は想定内の助言として記録に表示され、hookのエラーとは区別される。
- 判定の条件は観測できる事実（ツール呼び出しの有無など）に限り、意味（伝える内容の有無など）の判断はエージェント向け文書に残す。
- 記述言語のように本文から機械的に判定できる条件では、通知に該当箇所、判定基準、誤検出の時に本文を変えずに終える操作を書く。
  委譲元へ最終応答を返す主体は成果・判断・未達を指定の形式のまま全文で返せることを前提にし、判定を直す時は除外語を足す前にこの解除で回復できるかを確かめる。

### 入力

`Stop`と`SubagentStop`の共通入力は`session_id`、`prompt_id`、`transcript_path`、`cwd`、`permission_mode`、`effort`、`hook_event_name`である。
両イベントとも`stop_hook_active`、`last_assistant_message`、`background_tasks`、`session_crons`も受け取る。
`SubagentStop`は`agent_id`、`agent_type`、`agent_transcript_path`も受け取り、その`background_tasks`と`session_crons`は親セッション全体の範囲を表す。
`background_tasks`の各要素は`id`、`type`、`status`、`description`を持ち、`type`はタスクを作った機能（`shell`、`subagent`、`monitor`、`workflow`、`teammate`、`cloud session`、`MCP task`）を表す。
これでセッション全体の完了と、背景処理の完了待ちによる一時停止を区別する。
`PostToolUse`はバックグラウンド実行へ移った時点で発火し、ジョブの完了時の再発火は公式資料に記載が無い。
現行の版の入力に`background_tasks`が無ければ本項を失効とし、その版の観測で改訂する。

可視テキストの判定は、`last_assistant_message`と現行形式の記録からアシスタントの可視テキストだけを取り出して行う。
Claude Codeではアシスタントの`text`要素から取り出し、別の送信ツールを併用する時はそのツールが送った本文も同じ判定の対象に含める。
読めない記録や取得できない記録があっても未発話として扱わず、判定不能を判定のログに残して実行を許す。
作業工程の完了は記録の内容から推測せず、工程の実行と結果の確かな証拠で判定する。

### Codexの差分

Codexの`Stop`は、`decision: "block"`と`reason`を返すと同じターンを続け、許可する時は空のJSONオブジェクトを返す（`hookSpecificOutput`を受理しないため）。
遮断しない通知を送れない環境では、手順の支援は関連する操作の通知か文書に置き、通知を届けるためだけに遮断へ切り替えることを避ける。
Codex固有の入力に`model`がある。
Codexのrolloutの記録形式は安定した仕様ではないため、完了やバックグラウンドタスクの判定の入力に直接使わず、状態が欠けた時の復旧判定などで識別子の有無を確かめる用途に限ってランタイム別に変換して扱う。

### 委譲先での発火

`Stop`と`SubagentStop`の判定は、どちらも委譲先のセッションで発火しうる。
判定が求める処置を委譲先が実行できるかを判定ごとに確かめ、結果をコードとテストで確かめられる形にし、実行できない処置を求める判定は委譲先を対象から外す。

- `Stop`はそのセッション自身のターン終了で発火し、委譲先かどうかは実行環境が渡す識別情報で判別する。
- `SubagentStop`は`Agent`ツールのサブエージェントのターン終了だけで発火し、入力の`agent_id`で判別する。
  サブエージェントが実行できる処置だけを登録する。

## 子Claudeへ環境値を渡す

通常のClaude Code環境では、settingsの`env`がシェルの起動環境やAgent SDKの`options.env`の同名の変数を上書きする。
そのため、前置代入、`export`、`options.env`だけに頼らず、CLIの`--settings`（JSONファイルのパスかJSON文字列）かSDKの`settings`引数で値を渡す。
settingsの優先順位は高い順にmanaged、CLI、local、project、userで、managedはCLIの指定にも優先する。
実際に適用されるsettingsの`env`のキーを先に確かめ、ログと画面からは秘匿値を除く。
変数は丸ごと置き換わるため、`NO_PROXY`などのリストへ足す時は既存の値と結合した完全な文字列を渡す。
settingsには変数を削除する機能が無い。
空文字（`""`）はClaude Codeの接続先選択などでは未設定と同じ扱いになるが、Bashで実行するコマンド、MCPサーバー、hookへは空文字のまま渡るため、子プロセスへ渡したくない変数は起動元の環境でunsetする。
子プロセスが直接読むuser・project・localの`env`に同じ変数がある時に限り、CLIのsettingsの空文字で接続先選択から外せるが、子プロセスの中には空文字として残る。
Desktop版とSelf-hosted runnerでは、競合時に起動環境の値がsettingsより優先される。
根拠と最新の条件は、公式の[環境変数](https://code.claude.com/docs/en/env-vars)、[settingsの優先順](https://code.claude.com/docs/en/settings)、[settings-referenceのenv](https://code.claude.com/docs/en/settings-reference#env)で確かめる。

## auto modeのカスタムルール

Claude Codeのauto modeのカスタムルールは`~/.claude/settings.json`の`autoMode`の下に自然言語で書き、設定するキー名は公式スキーマ（`$schema`のURL）で実在を確かめる。
`allow`、`soft_deny`、`hard_deny`、`environment`の各配列を設定するとその区分のデフォルトの一覧が上書きされるため、デフォルトを保って足す時は配列に文字列`"$defaults"`を含める。
`allow`の許可ルールは、デフォルトの`soft_deny`による拒否を上書きする。

### ルール区分

- `allow`: 明示的に許可する操作
- `soft_deny`: 標準では拒否し、ユーザーの指示や文脈に応じて許可（`clears`）する操作
- `hard_deny`: どの状況でも拒否する操作
- `environment`: 信頼境界（対象のリポジトリ、ドメイン、バケット、クラウドサービスなど）の定義

### カスタムルール記述の注意点

- 対象の操作、対象のcommitの性質、対象のパスなどを文面で明確に示す。
- 粒度は操作の種別単位とし、分類器（classifier）の判別に要る代表例だけを添える。
  変わりやすい識別子（サブコマンド名、オプションの綴り、個別のファイルパスなど）の網羅的な列挙を避ける（ユーザー指定）。
- ルール文は許可する操作だけを簡潔に書き、それ以外の判定は`soft_deny`と`hard_deny`の標準の動作に任せる（ユーザー指定）。
  対象範囲は実際の作業の操作に合わせ、リモートのホスト、コードの由来、所有者、ユーザーの意図による過度な限定を避ける（意図の判定はUser Intent Ruleに任せる）。
  対象を狭めすぎると、正規の手順に沿った操作まで拒否されて作業が止まる。
- 上書きは`soft_deny`までにとどめ、デフォルトの`hard_deny`が守る領域を保つ（auto modeの安全境界を保つため）。
- 信頼対象（個人の管理リポジトリ、信頼するドメインなど）を指す`allow`ルールは、`autoMode.environment`で宣言した信頼境界と内容をそろえる。
